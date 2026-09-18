# AgentX

AgentX is a **Windows-first, local-first Adaptive Personal Operating Intelligence**
research system (an "AgentOS"). Its long-term goal is a runtime in which an agent
can operate a user's Windows machine, browser, and devices through governed,
verifiable capabilities, with persistent memory and the ability to learn and
repair its own procedures over time.

> **Current status: substantial governed research prototype; not a finished AgentOS.**
>
> Implemented code includes the Trusted Kernel, bounded agent/runtime strategies,
> persistent experience and semantic memory, procedure compilation/validation,
> reuse and repair machinery, an acceptance-verified Windows-native capability fabric, and browser capability foundations.
> These components do not yet establish the complete adaptive-learning workflow
> or a ready-to-use desktop product. The CLI currently exposes package metadata,
> not a natural-language agent session.
>
> See the [whole-project roadmap](docs/ROADMAP.md) for milestone exit criteria,
> the [status and completion ledger](docs/STATUS.md), and the independent
> [AX-001–AX-600 audit](docs/AUDIT_600.md) for evidence and remaining work.
> The exact normalized [600-task ledger](docs/TASKS.md) is generated from
> [machine-readable task records](docs/TASKS.json); historical reported status
> is separate from strict acceptance evidence.

## Requirements

- **Python 3.12 or newer** (3.12 and 3.13 are the targets).
- Windows 10/11 is the primary platform. No WSL, Docker, or POSIX shell is
  required. The code also runs on macOS/Linux, but Windows is what we design for.
- `git`.

The runtime package has **no third-party dependencies**. Development tooling
(`pytest`, `ruff`, `mypy`) is installed through the `dev` extra.

## Setup (Windows PowerShell)

```powershell
git clone https://github.com/harsh2025-sketch/AgentX.git
cd AgentX

# Create and activate a virtual environment (use the Python launcher to pick 3.12+).
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# If activation is blocked by execution policy, allow scripts for this user once:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Smoke test:

```powershell
python -m agentx --version
# agentx 0.0.1

agentx --help
```

On macOS/Linux, substitute `python3.12 -m venv .venv` and
`source .venv/bin/activate`; everything else is identical.

## Development commands

All commands are plain executables that work identically in PowerShell, `cmd`,
and POSIX shells. Run them from the repository root with the virtual
environment activated.

| Purpose              | Command                       |
| -------------------- | ----------------------------- |
| Run tests            | `python -m pytest`            |
| Lint                 | `ruff check .`                |
| Check formatting     | `ruff format --check .`       |
| Apply formatting     | `ruff format .`               |
| Type-check           | `mypy src/agentx`             |
| Type-check + tests   | `mypy`                        |

`scripts\check.ps1` runs the test, lint, format-check and type-check steps in
sequence and stops at the first failure:

```powershell
.\scripts\check.ps1
```

All configuration lives in `pyproject.toml`; there are no separate tool config
files.

## Repository layout

```text
AgentX/
├── pyproject.toml          Packaging + pytest/ruff/mypy configuration (single source)
├── README.md
├── .editorconfig           Editor whitespace / line-ending conventions
├── .gitattributes          LF normalisation (CRLF only for PowerShell/batch files)
├── .gitignore
├── src/agentx/             The `agentx` package (src-layout)
│   ├── __init__.py         Public namespace; exposes `__version__`
│   ├── __main__.py         `python -m agentx` entry point (argparse; --version, --help)
│   ├── _version.py         Single source of truth for the version
│   ├── _architecture.py    Canonical top-level boundary manifest (A1.02)
│   ├── py.typed            PEP 561 marker: the package is typed
│   └── <subsystems>/       Canonical ownership boundaries (see module map)
├── tests/
│   ├── unit/               In-process tests (import surface, version, CLI functions)
│   ├── architecture/       Dependency/boundary guardrail tests (A1.02)
│   └── integration/        Tests that run the CLI as a real child process
├── docs/architecture/      Architecture notes (boundaries + dependency direction)
└── scripts/                Developer utility scripts (PowerShell-first)
```

## Module map (ownership boundaries)

Each sub-package below is a canonical top-level ownership boundary. Production
modules exist in all eight packages. Top-level strategy/composition modules connect
these owners without introducing forbidden subsystem imports.

| Package                  | Responsibility for                                             | Status          |
| ------------------------ | -------------------------------------------------------------- | --------------- |
| `agentx.core`            | Shared contracts, task/execution state, evidence and world-state representations | implemented foundations |
| `agentx.kernel`          | Permissions, risk, budgets, action gating, stop, secrets and audit | implemented; hardening continues |
| `agentx.capabilities`    | Governed filesystem, Windows and browser operations | partial capability coverage |
| `agentx.hive`            | Knowledge, provenance, scope, preferences and retrieval contracts | implemented foundations |
| `agentx.procedures`      | Procedure graph/runtime, applicability and lifecycle | implemented; compilation/reuse/repair lifecycle accepted |
| `agentx.cognition`       | Reasoner, model roles, decomposition, routing and research contracts | implemented; concrete runtime gaps remain |
| `agentx.learning`        | Causal extraction, parameterization, synthesis and compilation | implemented foundations; live efficiency proof remains open |
| `agentx.infrastructure`  | Configuration, event journal, persistence and store adapters | implemented foundations |

The canonical names, allowed direct top-level imports, and forbidden dependency
examples are recorded in
[`docs/architecture/README.md`](docs/architecture/README.md) and are
machine-testable from `agentx._architecture`. Those rules are architecture
guardrails, not security enforcement; the Trusted Kernel is the security
authority.

The architectural invariants these boundaries are meant to protect are also
recorded in [`docs/architecture/README.md`](docs/architecture/README.md). They
require both implementation and regression evidence; passing a boundary test alone
does not prove a real-world task succeeds.

## Contributing conventions

- Python 3.12+ syntax and typing; `mypy --strict` must pass.
- `ruff check` and `ruff format --check` must pass.
- Use `pathlib` for paths; never assume POSIX paths, `/tmp`, symlinks, `chmod`,
  or a Unix shell. Subprocesses take argument lists and `sys.executable`,
  never `shell=True`.
- Do not add runtime dependencies "for later". Each dependency must be
  justified by the task that needs it.
- Do not add placeholder implementations of future subsystems.
