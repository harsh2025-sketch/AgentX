# AgentX architecture notes

> **Status:** bootstrap (task A1.01). This document records the *intended*
> subsystem boundaries and the invariants they exist to protect. It is **not**
> a design document and it does **not** describe implemented behaviour.
> Architecture is owned by the Technical Lead; changes to boundaries or
> invariants are made there, not in implementation tasks.

## Subsystem boundaries

The `agentx` package reserves one sub-package per subsystem. Reserving them now
means future work has an obvious, agreed home and cannot accidentally blur
responsibilities by landing in the wrong place.

| Package                 | Reserved for                                                   |
| ----------------------- | -------------------------------------------------------------- |
| `agentx.core`           | Agent Runtime and shared domain contracts                      |
| `agentx.kernel`         | Trusted Kernel                                                 |
| `agentx.capabilities`   | Capability Fabric (Windows / browser / device providers)       |
| `agentx.hive`           | Hive persistent memory                                         |
| `agentx.procedures`     | Procedure Graph runtime and Skill Compiler                     |
| `agentx.cognition`      | Provider-neutral cognitive models                              |
| `agentx.learning`       | Learning and repair systems                                    |
| `agentx.infrastructure` | Configuration, logging, event-driven observability plumbing    |

All of them are currently empty (docstring only), and
`tests/unit/test_package.py` fails if anything else is added to them outside
of a task that owns the subsystem.

## Invariants the boundaries must not make harder to implement

These are stated as constraints on future implementation. Nothing in the
bootstrap enforces them yet; the point is that nothing in the bootstrap should
*prevent* them either.

1. **Actions pass through the Trusted Kernel.** No subsystem gets a path to
   the machine that bypasses `agentx.kernel`.
2. **No action counts as success without verification.** Outcome checking is
   part of executing an action, not an optional afterthought.
3. **Models never receive unrestricted machine access.** `agentx.cognition`
   produces proposals; it does not hold capabilities.
4. **Capabilities are provider abstractions.** `agentx.capabilities` hides
   concrete Windows/browser/device mechanisms behind neutral interfaces.
5. **External content is untrusted.** Anything read from the outside world
   (web pages, files, model output) is data, never instructions with authority.
6. **Memory carries provenance.** `agentx.hive` records where every item came
   from and how much it can be trusted.
7. **Adaptive components do not control their own authority boundary.**
   `agentx.learning` may change *procedures*; it may never change *what it is
   allowed to do*. That decision belongs to the kernel.

## What bootstrap deliberately does not decide

- Event/message schemas and any event bus.
- Task/lifecycle state machines.
- The capability ABI.
- Storage engines for Hive.
- Model provider integrations.
- Inter-process or cross-language (Rust/C++) boundaries.

Each of these will be introduced by its own task with its own justification
and benchmarks. Until then, the dependency set stays at zero.
