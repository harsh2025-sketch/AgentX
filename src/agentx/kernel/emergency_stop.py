"""Process-local emergency-stop primitive for AgentX.

The stop is deliberately monotonic. Ordinary runtime code can request a stop
and observe it, but cannot clear, reset, resume, or re-arm the same object. A
fresh ``EmergencyStop`` instance is required for a fresh runtime lifecycle.

The stop is safety state, not authority. It grants no permission and performs
no action by itself.
"""

from __future__ import annotations

from enum import Enum
from threading import Event, Lock


class EmergencyStopState(Enum):
    """Canonical observable state of an :class:`EmergencyStop`."""

    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"


class EmergencyStop:
    """Thread-safe, monotonic process-local emergency stop.

    ``request_stop`` and ``try_admit_execution`` share one lock. This gives the
    runtime a single linearization point for the only race that matters at this
    boundary: either a privileged run is admitted before stop activation, or
    stop activation wins and that run must not begin. Admission does not grant
    authority and does not promise preemption of work that was already admitted.
    """

    __lock: Lock
    __requested: Event
    __slots__ = ("__lock", "__requested")

    def __init__(self) -> None:
        # ``object.__setattr__`` keeps construction explicit while the public
        # object exposes no assignment API for the safety state.
        object.__setattr__(self, "_EmergencyStop__requested", Event())
        object.__setattr__(self, "_EmergencyStop__lock", Lock())

    @property
    def state(self) -> EmergencyStopState:
        """Return the current monotonic stop state."""
        if self.__requested.is_set():
            return EmergencyStopState.STOP_REQUESTED
        return EmergencyStopState.RUNNING

    @property
    def stop_requested(self) -> bool:
        """Whether a stop has been requested."""
        return self.__requested.is_set()

    def try_admit_execution(self) -> bool:
        """Atomically admit one new run only while the stop is still clear.

        This is not an authorization decision. The caller must already have
        passed permission, risk, ActionGate, and other governed checks. The
        method exists only to close the final check/use gap against a concurrent
        :meth:`request_stop`.
        """
        with self.__lock:
            return not self.__requested.is_set()

    def request_stop(self) -> None:
        """Request the emergency stop. Repeated requests are idempotent."""
        with self.__lock:
            self.__requested.set()

    def __repr__(self) -> str:
        return f"EmergencyStop(state={self.state.value!r})"


__all__ = ["EmergencyStop", "EmergencyStopState"]
