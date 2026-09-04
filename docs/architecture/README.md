# AgentX architecture notes

> **Status:** A1.02 (module boundaries). This document records the canonical,
> explicit top-level dependency structure for the `agentx` package. It is not
> a full design document and it does not describe implemented behaviour.
> Architecture is owned by the Technical Lead; changes to boundaries or
> invariants are made there, not in implementation tasks.

## Canonical top-level boundaries

The `agentx` package reserves one sub-package per subsystem. Reserving them now
means future work has an obvious, agreed home and cannot accidentally blur
responsibilities by landing in the wrong place.

| Package                 | Responsibility                                                     |
| ----------------------- | ------------------------------------------------------------------ |
| `agentx.core`           | Shared domain contracts and Agent Runtime primitives                |
| `agentx.kernel`         | Trusted authority boundary: permissions, risk, budgets, capability gating, audit/security policy |
| `agentx.capabilities`   | Governed machine/browser/device capability implementations and contracts |
| `agentx.hive`           | Persistent semantic/episodic/procedural/causal/environmental knowledge |
| `agentx.procedures`     | Procedure Graph IR/runtime and skill lifecycle                     |
| `agentx.cognition`      | Provider-neutral reasoning/planning/model interfaces                |
| `agentx.learning`       | Research, skill compilation, reflection, repair and adaptive mechanisms |
| `agentx.infrastructure` | Configuration, events, persistence adapters and other non-domain plumbing |

These package *names* are stable and must not be renamed. Responsibilities are
intent, not implemented behaviour: none of these subsystems are implemented by
A1.02.

### Concrete specification

The machine-testable authority for this table is
`src/agentx/_architecture.py` (constants `SUBSYSTEMS`,
`ALLOWED_ARCHITECTURE_EDGES`). Do not duplicate the lists in new code; import
them from that module when a boundary must be consulted.

`agentx.infrastructure` is reserved for non-domain plumbing (configuration,
events, persistence adapters, logging). It is an implementation detail of other
subsystems, not a shared "everything" bag, and not an authority boundary.

The Trusted Kernel remains the *only* authority-granting layer. The boundary
model does **not** grant authority to `agentx.kernel`. It only reserves the
home for that layer and records which subsystems are allowed to be clients of
it.

## Dependency direction

The important rule is:

> **High-level adaptive components must not become authority.**
>
> In particular:
>
> - `agentx.cognition` must not directly own machine execution. Reasoning and
>   planning are providers of proposals; execution is governed.
> - `agentx.learning` must not grant permissions. Adaptive code may change
>   procedures, never its own authority boundary (*invariant* I10).
> - `agentx.hive` must not perform machine actions. Memory is data, never
>   authority.
> - `agentx.procedures` must not bypass kernel policy. Procedure graph
>   execution is routed through the kernel.
> - Capability implementations eventually execute only through governed runtime
>   paths.
>
> Avoid circular imports between major packages. Do **not** solve dependency
> direction with a giant shared `utils` package, and do not add generic
> abstractions merely because they might be useful later.

### Allowed direct imports (exact edges)

Each edge `(A, B)` means: code inside subsystem `A` may directly import from
subsystem `B`. These are the only allowed edges between canonical top-level
packages (the top-level `agentx` package and `agentx._architecture` are not
subsystems for this rule).

An allowed edge currently permits only the target subsystem package boundary
itself (for example `agentx.kernel`), **not** an arbitrary submodule inside it
(for example `agentx.kernel._internal` or an as-yet-unowned
`agentx.kernel.policy`). Cross-subsystem imports below that boundary are
treated as implementation internals and must be added to the boundary manifest
by the task that owns the target subsystem before they become legal.

| Package                 | May directly import                                       |
| ----------------------- | --------------------------------------------------------- |
| `agentx.core`           | (none)                                                   |
| `agentx.kernel`         | `agentx.core`, `agentx.infrastructure`                    |
| `agentx.capabilities`   | `agentx.core`, `agentx.kernel`                            |
| `agentx.hive`           | `agentx.core`, `agentx.infrastructure`                    |
| `agentx.procedures`     | `agentx.core`, `agentx.kernel`, `agentx.infrastructure`   |
| `agentx.cognition`      | `agentx.core`, `agentx.kernel`                            |
| `agentx.learning`       | `agentx.core`, `agentx.infrastructure`                    |
| `agentx.infrastructure` | (none)                                                   |

`agentx.core` is the shared-domain foundation; every other subsystem may build
on it. It is deliberately a leaf with respect to the other subsystems: core
must not import from `kernel`, `capabilities`, `hive`, `procedures`,
`cognition`, `learning`, or `infrastructure`. It is not a hub for arbitrary
cross-links.

### Forbidden direct imports (examples)

The following are architecture violations and are detected by the test in
`tests/architecture/test_module_boundaries.py`:

- `agentx.cognition` -> `agentx.capabilities` implementation internals
- `agentx.cognition` -> `agentx.kernel` implementation internals (the allowed
  `cognition -> kernel` edge does not permit importing *internals*; it permits
  only the public boundary, which here is defined as the `agentx.kernel`
  package itself)
- `agentx.hive` -> `agentx.capabilities`
- `agentx.hive` -> `agentx.cognition`
- `agentx.learning` -> `agentx.kernel` (authority-granting internals are
  forbidden; the allowed `learning -> core` / `learning -> infrastructure`
  edges do not change that)
- `agentx.infrastructure` -> `agentx.kernel` (or any other subsystem)
- `agentx.core` -> `agentx.kernel` / `agentx.capabilities` / `agentx.hive` /
  `agentx.procedures` / `agentx.cognition` / `agentx.learning` /
  `agentx.infrastructure`
- `agentx.capabilities` -> `agentx.cognition` / `agentx.hive` / `agentx.learning`
- `agentx.kernel` -> `agentx.cognition` / `agentx.hive` / `agentx.capabilities` / `agentx.learning`
- `agentx.cognition` -> `agentx.capabilities` / `agentx.hive` / `agentx.learning`
- any other edge not listed in the allowed set.

An intentional violation is planted in
`tests/architecture/violations/forbidden_edges.py` so the test proves the
checker actually fails on a forbidden edge.

> **Scope note.** These rules are import-level architecture guardrails only.
> They do not prevent indirect, in-process or delegated calls (for example a
> module that imports a *public* name and then calls into implementation
> details), and they are **not** security enforcement. Security is the job of
> the Trusted Kernel (`agentx.kernel`) and its own task, not of an import
> linter.

## Core contracts are not an authority hub

`agentx.core` will hold shared domain contracts and Agent Runtime primitives.
It is the shared foundation: every other subsystem may depend on it for
*data*, and `agentx.core` itself does not depend on the other subsystems. It
is not a permissions/authority mechanism, and no subsystem may use it to
bypass the kernel.

## Invariants the boundaries must not make harder to implement

These are stated as constraints on future implementation. A1.02 records them
for the dependency model; the bootstrap did not implement enforcement.

1. **No action counts as success without verification.** Outcome checking is
   part of executing an action, not an optional afterthought.
2. **Adaptive subsystems do not control their own authority boundary.**
   `agentx.learning` may change *procedures*; it may never change *what it is
   allowed to do*. That decision belongs to the kernel.
3. **External content is untrusted.** Anything read from the outside world
   (web pages, files, model output) is data, never instructions with authority.
4. **Every remembered claim eventually has provenance.** `agentx.hive` records
   where every item came from and how far it can be trusted.
5. **Every procedure eventually has scope/preconditions.** `agentx.procedures`
   is responsible for that contract; the kernel remains responsible for policy
   enforcement.
6. **Every autonomous work eventually has risk/resource limits.** Those limits
   are kernel policy, not a capability or a model.
7. **Deterministic execution beats model reasoning when safely sufficient.** A
   procedure is a candidate until verified and gated.
8. **Failed approaches are remembered.** Learning is a repository of
   candidates, not an authority.
9. **Newly learned skills are candidates, not trusted.** The kernel decides.
10. **Adaptive code cannot modify its authority boundary.** A dependency from
    `agentx.learning` to `agentx.kernel` is therefore forbidden.
11. **Important actions are auditable.** Audit/security policy belongs to
    `agentx.kernel`; non-domain plumbing in `agentx.infrastructure` must make
    audit records possible, never act as an alternative authority.
12. **Adaptive subsystems can be disabled for evaluation.** The boundary model
    must permit an evaluation harness to disable adaptive imports without
    changing the kernel.

## What A1.02 deliberately does not decide

A1.02 does not implement and does not define any of the following. They remain
owned by their own tasks and must not be implemented here:

- Event schema / taxonomy, EventBus, subscriptions, event journal (Codex-A, C1.02).
- Configuration loading/schema, environment-variable configuration, precedence,
  secrets handling (Codex-B, A1.03).
- SQLite, migrations.
- Task schema/state machine.
- Capability ABI.
- Trusted Kernel implementation.
- Hive implementation.
- Procedure Graph runtime or IR.
- Models, Windows/browser automation.

If one of those contracts is required by a boundary rule in the future, that
task must define the dependency requirement in this document and report it to
the Technical Lead, rather than implement the contract here.
