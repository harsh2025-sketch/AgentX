"""Governed, structured Windows application launch capability (N2.21).

This module owns the public contract only. Native process creation is an
injected adapter so the reviewed Windows seam can be bound later; this module
never parses commands, invokes a shell, or imports ctypes/Win32 APIs.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final, Protocol

from agentx.capabilities.abi import (
    CapabilityDescriptor, CapabilityIdentity, CapabilityName, CapabilityObservation, CapabilityParams,
    CapabilityPlatform, CapabilityPrecondition, CapabilityRequest, CapabilityScope,
    CapabilityVersion, ExecutionResult, ResourceEstimate, RollbackDeclaration,
    RollbackSupport, VerificationResult,
)
from agentx.core.execution import ExecutionContext
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

MAX_EXECUTABLE_LENGTH: Final = 2048
MAX_ARGUMENTS: Final = 128
MAX_ARGUMENT_LENGTH: Final = 4096
MAX_WORKING_DIRECTORY_LENGTH: Final = 2048


@dataclass(frozen=True, slots=True)
class ApplicationLaunchParams(CapabilityParams):
    """Structured launch input; argv is never re-tokenized or shell joined."""
    executable: str
    argv: tuple[str, ...]
    working_directory: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.executable, str) or not self.executable or len(self.executable) > MAX_EXECUTABLE_LENGTH:
            raise ValueError("executable must be a non-empty bounded string")
        if "\x00" in self.executable:
            raise ValueError("executable must not contain NUL")
        if not isinstance(self.argv, tuple):
            raise TypeError("argv must be an explicit tuple of strings")
        if len(self.argv) > MAX_ARGUMENTS:
            raise ValueError("argv exceeds the bounded argument count")
        for argument in self.argv:
            if not isinstance(argument, str):
                raise TypeError("argv entries must be strings")
            if "\x00" in argument:
                raise ValueError("argv entries must not contain NUL")
            if len(argument) > MAX_ARGUMENT_LENGTH:
                raise ValueError("argv entry exceeds the bounded length")
        if self.working_directory is not None:
            if not isinstance(self.working_directory, str) or not self.working_directory or len(self.working_directory) > MAX_WORKING_DIRECTORY_LENGTH:
                raise ValueError("working_directory must be a bounded non-empty string")
            if "\x00" in self.working_directory:
                raise ValueError("working_directory must not contain NUL")

    def to_dict(self) -> dict[str, object]:
        return {"executable": self.executable, "argv": list(self.argv), "working_directory": self.working_directory}


@dataclass(frozen=True, slots=True)
class NativeLaunchResult:
    """Low-level launch evidence, not readiness or application verification."""
    launched: bool
    process_id: int | None = None
    detail: str = ""
    def __post_init__(self) -> None:
        if type(self.launched) is not bool:
            raise TypeError("launched must be bool")
        if self.process_id is not None and (type(self.process_id) is not int or self.process_id <= 0):
            raise ValueError("process_id must be a positive integer when supplied")
        if not self.detail:
            object.__setattr__(self, "detail", "native launch reported success" if self.launched else "native launch failed")

class LaunchAdapter(Protocol):
    def launch(self, executable: str, argv: tuple[str, ...], working_directory: str | None = None) -> NativeLaunchResult: ...

APPLICATION_LAUNCH_V2_IDENTITY = CapabilityIdentity(
    name=CapabilityName("windows.application_launch"), version=CapabilityVersion(2, 0, 0)
)
_DESCRIPTOR = CapabilityDescriptor(
    identity=APPLICATION_LAUNCH_V2_IDENTITY,
    description="Launch one explicitly supplied Windows executable with structured argv.",
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.EXTERNAL_EFFECT}),
    risk_assessment=assess_risk(read_only=False, modifies_state=True, reversible=False, external_effect=True),
    preconditions=(CapabilityPrecondition(name="windows.platform", description="The execution platform must be Windows."),),
    rollback=RollbackDeclaration(support=RollbackSupport.UNSUPPORTED, detail="Process launch has no capability-owned rollback."),
    estimate=ResourceEstimate(timedelta(seconds=2), 1, Decimal("0")),
)

class WindowsApplicationLaunchV2Capability:
    """Structured launch implementation; authorization remains in the runtime."""
    def __init__(self, adapter: LaunchAdapter) -> None:
        self._adapter = adapter
    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _DESCRIPTOR
    def execute(self, request: CapabilityRequest[ApplicationLaunchParams], context: ExecutionContext) -> ExecutionResult:
        if not isinstance(request.params, ApplicationLaunchParams):
            raise TypeError("request requires ApplicationLaunchParams")
        if sys.platform != "win32":
            return self._failure("Windows application launch is unsupported on this platform")
        if context.observe_stop().should_stop:
            return self._failure("application launch cancelled by EmergencyStop")
        try:
            outcome = self._adapter.launch(request.params.executable, request.params.argv, request.params.working_directory)
        except Exception as exc:
            return self._failure(f"native launch adapter failed: {type(exc).__name__}")
        data: dict[str, object] = {"launched": outcome.launched, "detail": outcome.detail}
        if outcome.process_id is not None:
            data["process_id"] = outcome.process_id
        return ExecutionResult(outcome.launched, outcome.detail, CapabilityObservation("native launch evidence; readiness is unverified", data))
    @staticmethod
    def _failure(message: str) -> ExecutionResult:
        return ExecutionResult(False, message, CapabilityObservation("application launch did not execute", {"error": message}))
    def verify(self, request: CapabilityRequest[ApplicationLaunchParams], observation: CapabilityObservation, context: ExecutionContext) -> VerificationResult:
        if context.observe_stop().should_stop:
            return VerificationResult(False, "verification cancelled by EmergencyStop")
        return VerificationResult(False, "application launch provides no independent readiness verification")

__all__ = ["ApplicationLaunchParams", "APPLICATION_LAUNCH_V2_IDENTITY", "LaunchAdapter", "NativeLaunchResult", "WindowsApplicationLaunchV2Capability"]
