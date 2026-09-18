"""Governed explicit browser form interaction capabilities for M7.

The baseline browser action surface intentionally keeps fill separate from
submission.  This module extends that design with four explicit, typed
single-action operations:

* set one checkbox/radio state;
* select one explicit option value;
* submit through one already-selected submit control; and
* choose one local file for one already-selected file input.

All operations still run as ordinary canonical capabilities through
Executor/ActionGate.  The module performs no target discovery, JavaScript
execution, shell/process execution, browser launch, credential discovery, or
implicit submit.  File paths and field values are redacted from capability
serialization/evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePath
from typing import Final, Protocol

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.browser_actions import BrowserActionOutcome, BrowserClickPostcondition
from agentx.capabilities.browser_connection import (
    BrowserConnectionState,
    BrowserTargetKind,
    BrowserTargetRef,
    BrowserTargetState,
)
from agentx.capabilities.browser_dom import (
    BrowserDomNodeRef,
    BrowserDomNodeSnapshot,
    BrowserDomNodeState,
    BrowserDomObservation,
    BrowserDomObservationState,
    BrowserDomReadRequest,
)
from agentx.capabilities.browser_provider import BrowserProvider, BrowserProviderAvailability
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "BrowserFormDriver",
    "BrowserFormOperation",
    "BrowserFormParams",
    "BrowserFormsCapability",
    "select_option_request",
    "set_checked_request",
    "submit_selected_request",
    "upload_selected_request",
]

_MAX_OPTION_LENGTH: Final[int] = 4096
_MAX_PATH_LENGTH: Final[int] = 32_768
_RESOURCE_ESTIMATE: Final[ResourceEstimate] = ResourceEstimate(
    wall_clock=timedelta(seconds=5),
    machine_actions=1,
    external_cost=Decimal("0"),
)


class BrowserFormOperation(StrEnum):
    SET_CHECKED = "set_checked"
    SELECT_OPTION = "select_option"
    SUBMIT_SELECTED = "submit_selected"
    UPLOAD_SELECTED = "upload_selected"


def _identity(operation: BrowserFormOperation) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName(f"browser.forms.{operation.value}"),
        version=CapabilityVersion(1, 0, 0),
    )


def _error(code: str, message: str, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
    )


def _attribute(snapshot: BrowserDomNodeSnapshot, name: str) -> str | None:
    return next((item.value for item in snapshot.attributes if item.name == name), None)


def _matching_node(
    observation: BrowserDomObservation, node: BrowserDomNodeRef
) -> BrowserDomNodeSnapshot | None:
    return next(
        (item for item in observation.nodes if item.node.node_id == node.node_id),
        None,
    )


@dataclass(frozen=True, slots=True)
class BrowserFormParams(CapabilityParams):
    operation: BrowserFormOperation
    target: BrowserTargetRef
    selected_node: BrowserDomNodeRef
    checked: bool | None = None
    option_value: str | None = field(default=None, repr=False)
    file_path: str | None = field(default=None, repr=False)
    submit_postcondition: BrowserClickPostcondition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, BrowserFormOperation):
            raise TypeError("operation must be a BrowserFormOperation")
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError("target must be a BrowserTargetRef")
        if not isinstance(self.selected_node, BrowserDomNodeRef):
            raise TypeError("selected_node must be a BrowserDomNodeRef")
        if (
            self.selected_node.target.target_id != self.target.target_id
            or self.selected_node.target.session_id != self.target.session_id
        ):
            raise ValueError("selected_node must belong to the exact target/session")

        if self.operation is BrowserFormOperation.SET_CHECKED:
            if type(self.checked) is not bool:
                raise TypeError("set_checked requires a bool checked value")
            if any(
                value is not None
                for value in (self.option_value, self.file_path, self.submit_postcondition)
            ):
                raise ValueError("set_checked rejects unrelated form fields")
        elif self.operation is BrowserFormOperation.SELECT_OPTION:
            if not isinstance(self.option_value, str):
                raise TypeError("select_option requires option_value")
            if not self.option_value or self.option_value != self.option_value.strip():
                raise ValueError("option_value must be non-empty and trimmed")
            if len(self.option_value) > _MAX_OPTION_LENGTH:
                raise ValueError("option_value exceeds the bounded length")
            if any(
                value is not None
                for value in (self.checked, self.file_path, self.submit_postcondition)
            ):
                raise ValueError("select_option rejects unrelated form fields")
        elif self.operation is BrowserFormOperation.SUBMIT_SELECTED:
            if any(
                value is not None for value in (self.checked, self.option_value, self.file_path)
            ):
                raise ValueError("submit_selected rejects fill/select/upload fields")
            if self.submit_postcondition is not None and not isinstance(
                self.submit_postcondition, BrowserClickPostcondition
            ):
                raise TypeError("submit_postcondition must be BrowserClickPostcondition or None")
        elif self.operation is BrowserFormOperation.UPLOAD_SELECTED:
            if not isinstance(self.file_path, str):
                raise TypeError("upload_selected requires file_path")
            if not self.file_path or self.file_path != self.file_path.strip():
                raise ValueError("file_path must be non-empty and trimmed")
            if "\x00" in self.file_path or len(self.file_path) > _MAX_PATH_LENGTH:
                raise ValueError("file_path is invalid or exceeds the bounded length")
            if any(
                value is not None
                for value in (self.checked, self.option_value, self.submit_postcondition)
            ):
                raise ValueError("upload_selected rejects unrelated form fields")

    @property
    def upload_basename(self) -> str | None:
        if self.file_path is None:
            return None
        return PurePath(self.file_path.replace("\\", "/")).name

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": self.operation.value,
            "target": self.target.to_dict(),
            "selected_node": self.selected_node.to_dict(),
            "checked": self.checked,
            "option_value": None if self.option_value is None else "[REDACTED]",
            "file_path": None if self.file_path is None else "[REDACTED]",
            "upload_basename": self.upload_basename,
            "submit_postcondition": (
                None if self.submit_postcondition is None else self.submit_postcondition.to_dict()
            ),
            "action_count": 1,
            "implicit_submit": False,
        }


class BrowserFormDriver(Protocol):
    def set_checked(
        self, node: BrowserDomNodeRef, checked: bool
    ) -> Result[BrowserActionOutcome, AgentXError]: ...

    def select_option(
        self, node: BrowserDomNodeRef, option_value: str
    ) -> Result[BrowserActionOutcome, AgentXError]: ...

    def submit_selected(
        self, node: BrowserDomNodeRef
    ) -> Result[BrowserActionOutcome, AgentXError]: ...

    def upload_selected(
        self, node: BrowserDomNodeRef, file_path: str
    ) -> Result[BrowserActionOutcome, AgentXError]: ...

    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]: ...


def _descriptor(operation: BrowserFormOperation) -> CapabilityDescriptor:
    external = operation in {
        BrowserFormOperation.SUBMIT_SELECTED,
        BrowserFormOperation.UPLOAD_SELECTED,
    }
    return CapabilityDescriptor(
        identity=_identity(operation),
        description=f"Perform one explicit governed browser form operation: {operation.value}.",
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=(
            frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT})
            if external
            else frozenset({Permission.WRITE})
        ),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=external,
        ),
        preconditions=(),
        rollback=RollbackDeclaration(
            support=RollbackSupport.NOT_APPLICABLE,
            detail="Browser form mutations are not assumed reversible.",
        ),
        estimate=_RESOURCE_ESTIMATE,
    )


class BrowserFormsCapability:
    __slots__ = ("_descriptor", "_driver", "_operation", "_provider")

    def __init__(
        self,
        *,
        operation: BrowserFormOperation,
        provider: BrowserProvider,
        driver: BrowserFormDriver,
    ) -> None:
        if not isinstance(operation, BrowserFormOperation):
            raise TypeError("operation must be a BrowserFormOperation")
        if not hasattr(provider, "status") or not hasattr(provider, "descriptor"):
            raise TypeError("provider must implement BrowserProvider")
        self._operation = operation
        self._provider = provider
        self._driver = driver
        self._descriptor = _descriptor(operation)

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[BrowserFormParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        error = self._validate(request)
        if error is not None:
            params = request.params if isinstance(request.params, BrowserFormParams) else None
            return self._failed(error, params)
        if context.observe_stop().should_stop:
            return self._failed(
                _error(
                    "browser.forms.cancelled",
                    "browser form operation stopped before execution",
                    ErrorCategory.CANCELLED,
                ),
                request.params,
            )
        preflight = self._preflight(request.params)
        if preflight is not None:
            return self._failed(preflight, request.params)

        params = request.params
        try:
            if params.operation is BrowserFormOperation.SET_CHECKED:
                assert params.checked is not None
                result = self._driver.set_checked(params.selected_node, params.checked)
            elif params.operation is BrowserFormOperation.SELECT_OPTION:
                assert params.option_value is not None
                result = self._driver.select_option(params.selected_node, params.option_value)
            elif params.operation is BrowserFormOperation.SUBMIT_SELECTED:
                result = self._driver.submit_selected(params.selected_node)
            else:
                assert params.file_path is not None
                result = self._driver.upload_selected(params.selected_node, params.file_path)
        except Exception as exc:
            return self._failed(
                _error(
                    "browser.forms.provider_failure",
                    f"browser form driver raised {type(exc).__name__}",
                    ErrorCategory.EXECUTION,
                ),
                params,
            )
        if result.is_failure or not result.unwrap().succeeded:
            return self._failed(
                _error(
                    "browser.forms.provider_failure",
                    "browser form driver did not complete the requested operation",
                    ErrorCategory.EXECUTION,
                ),
                params,
            )
        return ExecutionResult(
            succeeded=True,
            message=f"{params.operation.value} accepted by the browser form driver",
            observation=CapabilityObservation(
                summary=f"browser {params.operation.value} invoked once",
                data={
                    **params.to_dict(),
                    "executed": True,
                    "verified": False,
                    "error": None,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[BrowserFormParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        error = self._validate(request)
        if error is not None:
            return VerificationResult(passed=False, detail=error.message)
        if context.observe_stop().should_stop:
            return VerificationResult(passed=False, detail="execution context stopped")
        if observation.data.get("executed") is not True:
            return VerificationResult(passed=False, detail="form operation was not executed")
        params = request.params
        observed = self._observe(params.target)
        if observed.is_failure:
            return VerificationResult(passed=False, detail="independent browser observation failed")
        snapshot = observed.unwrap()
        if snapshot.state is not BrowserDomObservationState.OBSERVED:
            return VerificationResult(passed=False, detail="browser DOM is not observed")

        if params.operation is BrowserFormOperation.SUBMIT_SELECTED:
            if params.submit_postcondition is None:
                return VerificationResult(
                    passed=False,
                    detail="submission executed but lacks an explicit observable postcondition",
                )
            return VerificationResult(
                passed=snapshot.target.url == params.submit_postcondition.expected_url,
                detail=(
                    "independent destination matches explicit submission postcondition"
                    if snapshot.target.url == params.submit_postcondition.expected_url
                    else "independent destination does not match submission postcondition"
                ),
            )

        node = _matching_node(snapshot, params.selected_node)
        if node is None or node.node.state is not BrowserDomNodeState.AVAILABLE:
            return VerificationResult(passed=False, detail="selected form node is unavailable")

        if params.operation is BrowserFormOperation.SET_CHECKED:
            expected = "true" if params.checked else None
            observed_checked = _attribute(node, "checked")
            passed = (
                observed_checked is not None
                if expected is not None
                else observed_checked is None
            )
            return VerificationResult(
                passed=passed,
                detail=(
                    "independent checked state matches request"
                    if passed
                    else "checked state mismatch"
                ),
            )
        if params.operation is BrowserFormOperation.SELECT_OPTION:
            passed = _attribute(node, "value") == params.option_value
            return VerificationResult(
                passed=passed,
                detail=(
                    "independent select value matches request"
                    if passed
                    else "select value mismatch"
                ),
            )

        expected_name = params.upload_basename
        observed_value = _attribute(node, "value") or ""
        normalized_value = observed_value.replace("\\", "/")
        passed = bool(expected_name) and (
            normalized_value == expected_name
            or normalized_value.endswith(f"/{expected_name}")
        )
        return VerificationResult(
            passed=passed,
            detail=(
                "independent file-input state matches selected filename"
                if passed
                else "upload field verification mismatch"
            ),
        )

    def _observe(
        self, target: BrowserTargetRef
    ) -> Result[BrowserDomObservation, AgentXError]:
        try:
            return self._driver.observe_dom(BrowserDomReadRequest(target=target))
        except Exception:
            return Result.failure(
                _error(
                    "browser.forms.verification_failure",
                    "browser form verification observation failed",
                    ErrorCategory.VERIFICATION,
                )
            )

    def _validate(
        self, request: CapabilityRequest[BrowserFormParams]
    ) -> AgentXError | None:
        if request.identity != self._descriptor.identity:
            return _error(
                "browser.forms.invalid_operation",
                "request identity does not match capability operation",
                ErrorCategory.VALIDATION,
            )
        if not isinstance(request.params, BrowserFormParams):
            return _error(
                "browser.forms.invalid_params",
                "params must be BrowserFormParams",
                ErrorCategory.VALIDATION,
            )
        if request.params.operation is not self._operation:
            return _error(
                "browser.forms.invalid_operation",
                "params operation does not match capability operation",
                ErrorCategory.VALIDATION,
            )
        return None

    def _preflight(self, params: BrowserFormParams) -> AgentXError | None:
        try:
            availability = self._provider.status.availability
        except Exception:
            availability = BrowserProviderAvailability.NOT_CONNECTED
        if availability is not BrowserProviderAvailability.AVAILABLE:
            return _error(
                "browser.forms.provider_unavailable",
                "browser provider is not available",
                ErrorCategory.DEPENDENCY,
            )
        target = params.target
        if (
            target.connection.state is not BrowserConnectionState.CONNECTED
            or target.state is not BrowserTargetState.AVAILABLE
            or target.kind is not BrowserTargetKind.PAGE
        ):
            return _error(
                "browser.forms.target_unavailable",
                "browser page/tab target is unavailable",
                ErrorCategory.PRECONDITION,
            )
        if params.selected_node.state is not BrowserDomNodeState.AVAILABLE:
            return _error(
                "browser.forms.stale_selection",
                "selected DOM node is stale or unavailable",
                ErrorCategory.PRECONDITION,
            )
        return None

    def _failed(
        self, error: AgentXError, params: BrowserFormParams | None
    ) -> ExecutionResult:
        data: dict[str, JsonValue] = {
            "operation": self._operation.value,
            "executed": False,
            "verified": False,
            "implicit_submit": False,
            "error": error.to_dict(),
        }
        if params is not None:
            data.update(params.to_dict())
        return ExecutionResult(
            succeeded=False,
            message=error.message,
            observation=CapabilityObservation(summary="browser form operation failed", data=data),
        )


def set_checked_request(
    target: BrowserTargetRef, selected_node: BrowserDomNodeRef, checked: bool
) -> CapabilityRequest[BrowserFormParams]:
    operation = BrowserFormOperation.SET_CHECKED
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserFormParams(
            operation=operation,
            target=target,
            selected_node=selected_node,
            checked=checked,
        ),
    )


def select_option_request(
    target: BrowserTargetRef, selected_node: BrowserDomNodeRef, option_value: str
) -> CapabilityRequest[BrowserFormParams]:
    operation = BrowserFormOperation.SELECT_OPTION
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserFormParams(
            operation=operation,
            target=target,
            selected_node=selected_node,
            option_value=option_value,
        ),
    )


def submit_selected_request(
    target: BrowserTargetRef,
    selected_node: BrowserDomNodeRef,
    *,
    postcondition: BrowserClickPostcondition | None = None,
) -> CapabilityRequest[BrowserFormParams]:
    operation = BrowserFormOperation.SUBMIT_SELECTED
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserFormParams(
            operation=operation,
            target=target,
            selected_node=selected_node,
            submit_postcondition=postcondition,
        ),
    )


def upload_selected_request(
    target: BrowserTargetRef, selected_node: BrowserDomNodeRef, file_path: str
) -> CapabilityRequest[BrowserFormParams]:
    operation = BrowserFormOperation.UPLOAD_SELECTED
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserFormParams(
            operation=operation,
            target=target,
            selected_node=selected_node,
            file_path=file_path,
        ),
    )
