"""Process-local monotonic emergency-stop signal for the AgentX Trusted Kernel.

The signal is a safety prerequisite only. Requesting it does not terminate
processes, kill threads, perform rollback, publish events, or alter permissions.
"""

from __future__ import annotations

from enum import Enum
from threading import Event


class EmergencyStopState(Enum):
    """Observable states of one emergency-stop instance."""

    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"


class EmergencyStop:
    """Thread-safe, process-local and monotonic emergency-stop boundary.

    The ordinary API intentionally has no reset/clear/resume operation. Trusted
    restart or reinitialization re-arms the system by constructing a new
    EmergencyStop instance.
    """

    __slots__ = ("__requested",)

    __requested: Event

    def __init__(self) -> None:
        object.__setattr__(self, "_EmergencyStop__requested", Event())

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

    def request_stop(self) -> None:
        """Monotonically request stop; repeated requests are idempotent."""

        self.__requested.set()

    def __repr__(self) -> str:
        return f"EmergencyStop(state={self.state.value!r})"


__all__ = ["EmergencyStop", "EmergencyStopState"]
