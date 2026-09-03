# AgentX

AgentX is a **Windows-first, local-first Adaptive Personal Operating Intelligence**
research system (an "AgentOS"). Its long-term goal is a runtime in which an agent
can operate a user's Windows machine, browser, and devices through governed,
verifiable capabilities, with persistent memory and the ability to learn and
repair its own procedures over time.

> **Current status: bootstrap only (task A1.01).**
>
> What exists today is a valid Python package, a version, a trivial
> command-line entry point, a test suite for those things, and lint/type-check
> configuration. **None of the architectural subsystems listed below are
> implemented.** Their packages are empty ownership boundaries. Do not expect
> any agent, model, memory, or automation behaviour from this repository yet.

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
│   ├── py.typed            PEP 561 marker: the package is typed
│   └── <subsystems>/       Empty ownership boundaries (see module map)
├── tests/
│   ├── unit/               In-process tests (import surface, version, CLI functions)
│   └── integration/        Tests that run the CLI as a real child process
├── docs/architecture/      Architecture notes (currently: subsystem map + invariants)
└── scripts/                Developer utility scripts (PowerShell-first)
```

## Module map (ownership boundaries)

Each sub-package below exists **only** to reserve a home for a future
subsystem. Each contains a docstring and nothing else; a test enforces that
they stay empty until a task explicitly implements them.

| Package                  | Future owner of                                                  | Status          |
| ------------------------ | ---------------------------------------------------------------- | --------------- |
| `agentx.core`            | Agent runtime and shared domain contracts                        | not implemented |
| `agentx.kernel`          | Trusted Kernel: policy, authority boundaries, verification        | not implemented |
| `agentx.capabilities`    | Capability Fabric: Windows / browser / device provider abstractions | not implemented |
| `agentx.hive`            | Hive persistent memory with provenance                           | not implemented |
| `agentx.procedures`      | Procedure Graph runtime and Skill Compiler                       | not implemented |
| `agentx.cognition`       | Provider-neutral cognitive model integration                     | not implemented |
| `agentx.learning`        | Learning and repair systems                                      | not implemented |
| `agentx.infrastructure`  | Configuration, logging, event-driven observability plumbing      | not implemented |

The architectural invariants these boundaries are meant to protect are recorded
in [`docs/architecture/README.md`](docs/architecture/README.md). They describe
intent, not implemented behaviour.

## Contributing conventions (bootstrap)

- Python 3.12+ syntax and typing; `mypy --strict` must pass.
- `ruff check` and `ruff format --check` must pass.
- Use `pathlib` for paths; never assume POSIX paths, `/tmp`, symlinks, `chmod`,
  or a Unix shell. Subprocesses take argument lists and `sys.executable`,
  never `shell=True`.
- Do not add runtime dependencies "for later". Each dependency must be
  justified by the task that needs it.
- Do not add placeholder implementations of future subsystems.
