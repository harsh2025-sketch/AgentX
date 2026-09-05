"""The AgentX Executor boundary (A2.04).

The Executor is the narrow runtime boundary through which higher orchestration
(later A2.06 Task Manager, A2.07 Router) will *request* execution of one
already-selected capability. It is deliberately the smallest possible
component:

    ExecutorRequest(task, capability_request, context)
        -> CapabilityExecutionLoop.run(...)   # canonical A1.10
        -> ClosedLoopOutcome                  # canonical A1.10 result

It is **not** a second execution authority
------------------------------------------

Every governed step — registry resolution, permission evaluation, ActionGate
decision, effective-risk floor, emergency stop, cooperative cancellation and
deadline observation, atomic resource-budget consumption, capability
``execute``/``verify``, canonical Task transitions, canonical events, and
canonical audit records — happens exactly once, inside the canonical A1.10
:class:`~agentx.capabilities.runtime.CapabilityExecutionLoop`. This module
contains no ActionGate path, no permission engine, no risk engine, no resource
budget, no emergency-stop path, no capability registry lookup, and no
execute/verify loop of its own.

The governing invariant is inherited, never re-implemented::

    NO ACTION == SUCCESS WITHOUT VERIFICATION.

The Executor cannot mark success. It never constructs a
:class:`~agentx.capabilities.abi.VerificationResult`, never transitions a
:class:`~agentx.core.tasks.Task`, and never rewrites a
:class:`~agentx.capabilities.runtime.ClosedLoopOutcome`. The outcome object it
returns is the identical object the canonical loop produced, so
``outcome.verified``, ``outcome.task.status``, the execution evidence, the
observation, the verification evidence, the failure/denial error, and the
budget usage are all canonical A1.10 values.

Authority boundary
------------------

The Executor holds no authority and can manufacture none. It does not accept,
build, or widen an :class:`~agentx.kernel.permissions.AuthorityContext`; it
does not construct a gate request; it cannot lower effective risk, enlarge a
:class:`~agentx.kernel.resource_budget.ResourceEnvelope`, or clear an
:class:`~agentx.kernel.emergency_stop.EmergencyStop`. Those collaborators are
injected into the A1.10 loop at composition time and the Executor never sees
them. Hostile text carried in a Task objective, capability params, or
capability metadata is inert data: it flows through unchanged and changes no
decision.

No cognition, no routing
------------------------

The Executor performs no reasoning, planning, model call, research, or Hive
interaction, and imports no cognition module. It also performs no routing: it
does not choose a strategy level, does not select a capability, does not
retry, does not fall back, does not escalate, and does not loop. It executes
the one explicit request it is handed, once, and reports what the canonical
path returned.

Error behaviour
---------------

Malformed or internally inconsistent requests fail explicitly at
:class:`ExecutorRequest` construction (wrong types raise :class:`TypeError`;
inconsistent task/context identity raises :class:`ExecutorRequestError`).
Every canonical denial or failure is propagated verbatim as the A1.10
``Result`` — never swallowed, never downgraded, never retried.

Deliberate non-scope
--------------------

Not a Verifier (A2.05), Task Manager (A2.06), Router (A2.07), escalation
(A2.08), anti-loop (A2.09), or agent loop (A2.10). No procedure-graph
interpreter, skill compiler, repair, research, or concrete provider.

Owner: A2.04. Belongs to ``agentx.capabilities`` — the same canonical
subsystem that owns the governed execution path it delegates to — so it adds
no top-level package and widens no boundary edge. It imports only the standard
library, canonical ``agentx.core`` contracts, and the canonical sibling A1.10
runtime module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome
from agentx.core.errors import AgentXError
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task

__all__ = [
    "EXECUTOR_SOURCE",
    "Executor",
    "ExecutorRequest",
    "ExecutorRequestError",
]

#: Stable identifier of this boundary, for reporting and documentation. The
#: Executor publishes no events of its own; canonical evidence is emitted by
#: the A1.10 loop under its own source.
EXECUTOR_SOURCE: Final[str] = "agentx.capabilities.executor"


class ExecutorRequestError(ValueError):
    """Raised when an execution request is internally inconsistent.

    This is a request-shape error only. It never carries, encodes, or implies
    an authority decision: authority denials are canonical A1.10 outcomes.
    """


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    """The smallest typed execution request the Executor accepts.

    It carries exactly the three canonical values the governed path needs and
    nothing else: the canonical :class:`~agentx.core.tasks.Task`, the canonical
    :class:`~agentx.capabilities.abi.CapabilityRequest` naming the
    already-selected capability, and the canonical
    :class:`~agentx.core.execution.ExecutionContext`.

    There is deliberately no mutable metadata mapping, no permission field, no
    risk or budget override, no strategy/level hint, no retry policy, and no
    verification field. Anything of that shape would be an authority decision
    smuggled into a data object.

    Validation is structural only: types must be canonical, and when the
    context declares a ``task_id`` it must be the requested Task's identity.
    """

    task: Task
    capability_request: CapabilityRequest[Any]
    context: ExecutionContext

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(f"task must be a Task, got {type(self.task).__name__}")
        if not isinstance(self.capability_request, CapabilityRequest):
            raise TypeError(
                "capability_request must be a CapabilityRequest, got "
                f"{type(self.capability_request).__name__}"
            )
        if not isinstance(self.context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(self.context).__name__}"
            )
        if self.context.task_id is not None and self.context.task_id != self.task.task_id:
            raise ExecutorRequestError(
                "execution context task identity does not match the requested Task"
            )


class Executor:
    """Requests governed execution of one already-selected capability.

    The Executor owns a single canonical collaborator: the A1.10
    :class:`~agentx.capabilities.runtime.CapabilityExecutionLoop`. That loop is
    the only execution mechanism it can reach, so every run is governed,
    audited, and verified by canonical machinery.

    The object is stateless between calls (it keeps no counters, caches,
    histories, or in-flight registries), which is what makes "no hidden retry"
    a structural property rather than a policy.
    """

    __slots__ = ("_execution_loop",)

    def __init__(self, *, execution_loop: CapabilityExecutionLoop) -> None:
        """Bind the canonical governed execution path.

        Only a real :class:`CapabilityExecutionLoop` is accepted. The Executor
        must not be able to be pointed at an ungoverned "execute this" callable.
        """
        if not isinstance(execution_loop, CapabilityExecutionLoop):
            raise TypeError(
                "execution_loop must be a CapabilityExecutionLoop, got "
                f"{type(execution_loop).__name__}"
            )
        self._execution_loop = execution_loop

    @property
    def execution_loop(self) -> CapabilityExecutionLoop:
        """The canonical A1.10 path this Executor delegates to."""
        return self._execution_loop

    def execute(self, request: ExecutorRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        """Delegate one explicit execution request to the canonical A1.10 path.

        The canonical loop is invoked exactly once with the exact canonical
        Task, capability request, and execution context carried by ``request``.
        Its :class:`~agentx.core.result.Result` — success outcome or refusal
        error — is returned unchanged and unwrapped by this boundary.

        Raises :class:`TypeError` for a non-:class:`ExecutorRequest` argument,
        which is a programming error rather than a runtime outcome. Exceptions
        raised by the canonical path are never caught here: swallowing them
        would hide execution errors.
        """
        if not isinstance(request, ExecutorRequest):
            raise TypeError(f"request must be an ExecutorRequest, got {type(request).__name__}")

        return self._execution_loop.run(
            request.task,
            request.capability_request,
            request.context,
        )
