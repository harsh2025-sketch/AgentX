"""Process-local monotonic emergency-stop signal for the AgentX Trusted Kernel.

The signal is a safety prerequisite only. Requesting it does not terminate
processes, kill threads, perform rollback, publish events, or alter permissions.
Execution admission and stop activation are serialized so a governed caller can
establish an unambiguous ordering at the final pre-execution boundary.
"""

from __future__ import annotations

from enum import Enum
from threading import Event, Lock


class EmergencyStopState(Enum):
    """Observable states of one emergency-stop instance."""

    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"


class EmergencyStop:
    """Thread-safe, process-local and monotonic emergency-stop boundary.

    The ordinary API intentionally has no reset/clear/resume operation. Trusted
    restart or reinitialization re-arms the system by constructing a new
    EmergencyStop instance.

    ``try_admit_execution`` is the canonical linearization point for a governed
    run immediately before it becomes in-flight. It grants no permission or
    authority. A ``True`` result means this run was admitted before any
    concurrent stop request; a stop request that linearizes first makes the
    admission fail closed. Once admitted, the run is already in-flight and a
    later stop request applies to subsequent admissions rather than pretending
    to preempt an effect that has already begun.
    """

    __slots__ = ("__lock", "__requested")

    def __init__(self) -> None:
        object.__setattr__(self, "_EmergencyStop__requested", Event())
        object.__setattr__(self, "_EmergencyStop__lock", Lock())

    @property
    def state(self) -> EmergencyStopState:
        """Return the current safety state."""

        if self.__requested.is_set():
            return EmergencyStopState.STOP_REQUESTED
        return EmergencyStopState.RUNNING

    @property
    def stop_requested(self) -> bool:
        """Cheaply observe whether stop has been requested."""

        return self.__requested.is_set()

    def try_admit_execution(self) -> bool:
        """Atomically admit one governed run only while the stop is inactive.

        This method is deliberately not an authorization API. Permission, risk,
        confirmation and resource policy remain separate Trusted-Kernel gates.
        Callers use this once at the final safety boundary before resource
        consumption and external execution can begin.
        """

        with self.__lock:
            return not self.__requested.is_set()

    def request_stop(self) -> None:
        """Monotonically request stop; repeated requests are idempotent."""

        with self.__lock:
            self.__requested.set()

    def __repr__(self) -> str:
        return f"EmergencyStop(state={self.state.value!r})"


__all__ = ["EmergencyStop", "EmergencyStopState"]
