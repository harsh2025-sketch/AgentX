"""M7.01 governed browser action capability boundary.

This module is the smallest production-quality mutation surface for already
established canonical browser connection/session/tab identity and already
selected DOM node identity. It does not plan, does not resolve elements from
natural language, and does not bypass the CapabilityExecutionLoop.

Canonical operations
--------------------

Exactly one operation is accepted per request:

* ``navigate`` — explicit ``http``/``https`` URL, bound to one C5.02 target.
* ``click_selected`` — bound to one C5.03 selected node reference.
* ``fill_selected`` — bound to one C5.03 selected node reference plus inert text.

``submit_selected`` is intentionally absent: baseline C5.01/C5.02/C5.03
contracts expose no submit mechanism, and this module will not invent a second
action channel.

Execution boundary
------------------

Actions are delegated to an injected :class:`BrowserActionDriver` that is
expected to speak to an already-established C5.01 provider/session. This module
does not launch browsers, open sockets, spawn processes, evaluate JavaScript,
or accept arbitrary CDP/script passthrough.

Verification
------------

A driver return is observation evidence only. ``verify`` independently
re-observes document/node state through the same driver:

* navigate — observed document URL must match the requested URL.
* fill_selected — observed node value/text must match the requested inert text.
* click_selected — never treated as verified merely because click returned.
  Verification requires an explicit request-level postcondition; otherwise the
  result is executed-but-not-verified.

Webpage strings, fill text, and URLs are untrusted data. They cannot change
permission, risk, budget, emergency stop, verification, or operation identity.

Owner: M7.01. Belongs to ``agentx.capabilities``. Imports only the standard
library, canonical ``agentx.core`` / ``agentx.kernel`` contracts, the
capability ABI, and the existing C5.01-C5.03 browser identity/observation
contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityPrecondition,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
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
from agentx.capabilities.browser_provider import (
    BrowserProvider,
    BrowserProviderAvailability,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "BROWSER_CLICK_SELECTED_IDENTITY",
    "BROWSER_FILL_SELECTED_IDENTITY",
    "BROWSER_NAVIGATE_IDENTITY",
    "MAX_BROWSER_ACTION_FILL_LENGTH",
    "MAX_BROWSER_ACTION_URL_LENGTH",
    "SUPPORTED_BROWSER_ACTION_SCHEMES",
    "BrowserActionDriver",
    "BrowserActionErrorCode",
    "BrowserActionOperation",
    "BrowserActionOutcome",
    "BrowserActionParams",
    "BrowserActionValidationError",
    "BrowserActionsCapability",
    "BrowserClickPostcondition",
    "click_selected_request",
    "fill_selected_request",
    "navigate_request",
]

BROWSER_NAVIGATE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("browser.actions.navigate"),
    version=CapabilityVersion(1, 0, 0),
)
BROWSER_CLICK_SELECTED_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("browser.actions.click_selected"),
    version=CapabilityVersion(1, 0, 0),
)
BROWSER_FILL_SELECTED_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("browser.actions.fill_selected"),
    version=CapabilityVersion(1, 0, 0),
)

MAX_BROWSER_ACTION_URL_LENGTH: Final[int] = 8192
MAX_BROWSER_ACTION_FILL_LENGTH: Final[int] = 65_536
SUPPORTED_BROWSER_ACTION_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_FORBIDDEN_SCHEMES: Final[frozenset[str]] = frozenset(
    {
        "file",
        "javascript",
        "data",
        "shell",
        "powershell",
        "vbscript",
        "about",
        "blob",
        "ws",
        "wss",
        "ftp",
        "chrome",
        "chrome-extension",
        "edge",
        "view-source",
    }
)
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")
_RESOURCE_ESTIMATE: Final[ResourceEstimate] = ResourceEstimate(
    wall_clock=timedelta(seconds=5),
    machine_actions=1,
    external_cost=Decimal("0"),
)


class BrowserActionValidationError(ValueError):
    """Raised when a browser-action contract value is malformed."""


class BrowserActionErrorCode(StrEnum):
    """Stable operational error codes for the browser-action capability."""

    INVALID_OPERATION = "browser.actions.invalid_operation"
    INVALID_URL = "browser.actions.invalid_url"
    UNSUPPORTED_SCHEME = "browser.actions.unsupported_scheme"
    MISSING_CONNECTION = "browser.actions.missing_connection"
    MISSING_TAB = "browser.actions.missing_tab"
    STALE_SELECTION = "browser.actions.stale_selection"
    INVALID_SELECTION = "browser.actions.invalid_selection"
    PROVIDER_UNAVAILABLE = "browser.actions.provider_unavailable"
    PROVIDER_EXECUTION_FAILURE = "browser.actions.provider_execution_failure"
    VERIFICATION_FAILURE = "browser.actions.verification_failure"
    CANCELLED = "browser.actions.cancelled"
    MALFORMED_PARAMS = "browser.actions.malformed_params"


class BrowserActionOperation(StrEnum):
    """Closed vocabulary of governed browser mutations. One per request."""

    NAVIGATE = "navigate"
    CLICK_SELECTED = "click_selected"
    FILL_SELECTED = "fill_selected"


def _json_object(value: object) -> dict[str, JsonValue]:
    """Return a JSON-object mapping, failing closed on unexpected shapes."""
    if not isinstance(value, dict):
        raise BrowserActionValidationError("expected a JSON object")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise BrowserActionValidationError(f"non-JSON-compatible value of type {type(value).__name__}")


def _redact_url(url: str) -> str:
    """Strip userinfo from a URL so credentials cannot leak through errors."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _classify_url(value: object) -> tuple[str | None, BrowserActionErrorCode | None, str | None]:
    """Validate an explicit navigation URL. Returns ``(url, error_code, message)``."""
    if not isinstance(value, str):
        return None, BrowserActionErrorCode.MALFORMED_PARAMS, "url must be a string"
    if not value or value != value.strip():
        return None, BrowserActionErrorCode.INVALID_URL, "url must be non-empty and trimmed"
    if any(character in value for character in _CONTROL_CHARACTERS):
        return (
            None,
            BrowserActionErrorCode.INVALID_URL,
            "url must not contain control characters",
        )
    if any(character.isspace() for character in value):
        return None, BrowserActionErrorCode.INVALID_URL, "url must not contain whitespace"
    if len(value) > MAX_BROWSER_ACTION_URL_LENGTH:
        return (
            None,
            BrowserActionErrorCode.INVALID_URL,
            f"url must not exceed {MAX_BROWSER_ACTION_URL_LENGTH} characters",
        )
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if not scheme:
        return None, BrowserActionErrorCode.INVALID_URL, "url must include an explicit scheme"
    if scheme in _FORBIDDEN_SCHEMES or scheme not in SUPPORTED_BROWSER_ACTION_SCHEMES:
        return (
            None,
            BrowserActionErrorCode.UNSUPPORTED_SCHEME,
            f"url scheme {scheme!r} is not supported; only http and https are allowed",
        )
    if not parts.netloc or not (parts.hostname or "").strip():
        return None, BrowserActionErrorCode.INVALID_URL, "url must include an explicit host"
    return value, None, None


def _require_url(value: object, *, field_name: str) -> str:
    url, code, message = _classify_url(value)
    if url is None:
        assert message is not None
        if code is BrowserActionErrorCode.UNSUPPORTED_SCHEME:
            raise BrowserActionValidationError(message)
        raise BrowserActionValidationError(f"{field_name}: {message}")
    return url


def _urls_match(expected: str, observed: str | None) -> bool:
    if observed is None or not isinstance(observed, str):
        return False
    if expected == observed:
        return True
    left = urlsplit(expected)
    right = urlsplit(observed)
    return (
        left.scheme.lower() == right.scheme.lower()
        and (left.hostname or "").lower() == (right.hostname or "").lower()
        and left.port == right.port
        and (left.path or "/") == (right.path or "/")
        and left.query == right.query
        and left.fragment == right.fragment
    )


def _node_filled_value(snapshot: BrowserDomNodeSnapshot) -> str | None:
    for attribute in snapshot.attributes:
        if attribute.name == "value":
            return attribute.value
    return snapshot.text


def _error(
    code: BrowserActionErrorCode,
    message: str,
    *,
    category: ErrorCategory,
    details: dict[str, JsonValue] | None = None,
) -> AgentXError:
    return AgentXError(
        code=code.value,
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
        details=details,
    )


@dataclass(frozen=True, slots=True)
class BrowserClickPostcondition:
    """Explicit, independently observable postcondition for a click.

    A provider click return is never itself a verdict. When this object is
    present, ``verify`` re-observes the document URL and compares it to
    ``expected_url``. Absence means executed-but-not-verified.
    """

    expected_url: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_url",
            _require_url(self.expected_url, field_name="click postcondition expected_url"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"expected_url": _redact_url(self.expected_url)}


@dataclass(frozen=True, slots=True)
class BrowserActionParams(CapabilityParams):
    """Typed parameters for exactly one governed browser action.

    Natural-language instructions are not a targeting mechanism. The request
    must already carry canonical C5.02 target identity and, for click/fill,
    a canonical C5.03 selected node identity.
    """

    operation: BrowserActionOperation
    target: BrowserTargetRef
    url: str | None = None
    selected_node: BrowserDomNodeRef | None = None
    text: str | None = None
    click_postcondition: BrowserClickPostcondition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, BrowserActionOperation):
            raise TypeError(
                f"operation must be a BrowserActionOperation, got {type(self.operation).__name__}"
            )
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError(f"target must be a BrowserTargetRef, got {type(self.target).__name__}")
        if self.click_postcondition is not None and not isinstance(
            self.click_postcondition, BrowserClickPostcondition
        ):
            raise TypeError(
                "click_postcondition must be a BrowserClickPostcondition or None, "
                f"got {type(self.click_postcondition).__name__}"
            )

        if self.operation is BrowserActionOperation.NAVIGATE:
            if self.selected_node is not None or self.text is not None:
                raise BrowserActionValidationError(
                    "navigate accepts an explicit URL only; extra action fields are rejected"
                )
            if self.click_postcondition is not None:
                raise BrowserActionValidationError("navigate does not accept a click postcondition")
            object.__setattr__(self, "url", _require_url(self.url, field_name="url"))
            return

        if self.url is not None:
            raise BrowserActionValidationError(
                f"{self.operation.value} does not accept a url field"
            )
        if self.selected_node is None:
            raise BrowserActionValidationError(
                f"{self.operation.value} requires an explicit selected DOM node"
            )
        if not isinstance(self.selected_node, BrowserDomNodeRef):
            raise TypeError(
                "selected_node must be a BrowserDomNodeRef, "
                f"got {type(self.selected_node).__name__}"
            )

        if self.operation is BrowserActionOperation.CLICK_SELECTED:
            if self.text is not None:
                raise BrowserActionValidationError(
                    "click_selected does not accept fill text and does not submit"
                )
            return

        if self.operation is BrowserActionOperation.FILL_SELECTED:
            if self.click_postcondition is not None:
                raise BrowserActionValidationError(
                    "fill_selected does not accept a click postcondition and does not submit"
                )
            if not isinstance(self.text, str):
                raise TypeError("fill text must be a string")
            if len(self.text) > MAX_BROWSER_ACTION_FILL_LENGTH:
                raise BrowserActionValidationError(
                    f"fill text must not exceed {MAX_BROWSER_ACTION_FILL_LENGTH} characters"
                )
            if "\x00" in self.text:
                raise BrowserActionValidationError("fill text must not contain NUL characters")
            return

        raise BrowserActionValidationError(f"unsupported browser action {self.operation!r}")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "operation": self.operation.value,
            "target": _json_object(self.target.to_dict()),
            "url": None if self.url is None else _redact_url(self.url),
            "selected_node": (
                None if self.selected_node is None else _json_object(self.selected_node.to_dict())
            ),
            "text": self.text,
            "click_postcondition": (
                None if self.click_postcondition is None else self.click_postcondition.to_dict()
            ),
            "action_count": 1,
            "implicit_submit": False,
        }
        return payload


@dataclass(frozen=True, slots=True)
class BrowserActionOutcome:
    """Driver-supplied execution evidence. Never a verification verdict."""

    succeeded: bool
    message: str
    observed_target: BrowserTargetRef | None = None
    observed_node: BrowserDomNodeSnapshot | None = None

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise TypeError(f"succeeded must be bool, got {type(self.succeeded).__name__}")
        if (
            not isinstance(self.message, str)
            or not self.message
            or self.message != self.message.strip()
        ):
            raise BrowserActionValidationError("outcome message must be a non-empty trimmed string")
        if self.observed_target is not None and not isinstance(
            self.observed_target, BrowserTargetRef
        ):
            raise TypeError("observed_target must be a BrowserTargetRef or None")
        if self.observed_node is not None and not isinstance(
            self.observed_node, BrowserDomNodeSnapshot
        ):
            raise TypeError("observed_node must be a BrowserDomNodeSnapshot or None")


class BrowserActionDriver(Protocol):
    """Narrow already-connected mutation/observation port.

    Implementations must not expose JavaScript evaluation, CDP passthrough,
    process launch, or multi-action chaining. ``observe_dom`` is the
    independent verification read; it is not authorization.
    """

    def navigate(
        self, target: BrowserTargetRef, url: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        """Navigate ``target`` to an already-validated http(s) URL."""
        ...

    def click_selected(self, node: BrowserDomNodeRef) -> Result[BrowserActionOutcome, AgentXError]:
        """Click one already-selected DOM node. Does not evaluate page scripts."""
        ...

    def fill_selected(
        self, node: BrowserDomNodeRef, text: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        """Fill one already-selected DOM node with inert text. Does not submit."""
        ...

    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]:
        """Return one independent structured DOM/document snapshot."""
        ...


def _identity_for(operation: BrowserActionOperation) -> CapabilityIdentity:
    if operation is BrowserActionOperation.NAVIGATE:
        return BROWSER_NAVIGATE_IDENTITY
    if operation is BrowserActionOperation.CLICK_SELECTED:
        return BROWSER_CLICK_SELECTED_IDENTITY
    return BROWSER_FILL_SELECTED_IDENTITY


def _permissions_for(operation: BrowserActionOperation) -> frozenset[Permission]:
    if operation is BrowserActionOperation.CLICK_SELECTED:
        return frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT})
    return frozenset({Permission.WRITE})


def _description_for(operation: BrowserActionOperation) -> str:
    if operation is BrowserActionOperation.NAVIGATE:
        return "Navigate one already-identified browser page/tab to an explicit http or https URL."
    if operation is BrowserActionOperation.CLICK_SELECTED:
        return (
            "Click one already-selected DOM node on an already-identified browser page/tab. "
            "Click may cause further external effects; a driver return is not verification."
        )
    return (
        "Fill one already-selected DOM node with caller-supplied inert text. "
        "The text is data only and never implies submit, permission, or verification."
    )


def _descriptor_for(operation: BrowserActionOperation) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=_identity_for(operation),
        description=_description_for(operation),
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=_permissions_for(operation),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=False,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="browser.connection.connected",
                description=(
                    "An already-established C5.02 connected browser session snapshot must "
                    "identify the target page or tab."
                ),
            ),
            CapabilityPrecondition(
                name="browser.target.available",
                description="The C5.02 target snapshot must be an available page/tab.",
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.UNSUPPORTED,
            detail=(
                "Browser mutations are not rolled back by this capability; navigation, "
                "click, and fill are treated as irreversible at this boundary."
            ),
        ),
        estimate=_RESOURCE_ESTIMATE,
    )


class BrowserActionsCapability:
    """One governed browser mutation as a canonical Capability.

    Construct one instance per operation. The descriptor is fixed at
    construction from the operation identity; webpage content and fill text
    cannot change permission, risk, or identity. The CapabilityExecutionLoop
    remains the only authority path: this object never invokes kernel gating.
    """

    __slots__ = ("_descriptor", "_driver", "_operation", "_provider")

    def __init__(
        self,
        *,
        operation: BrowserActionOperation,
        provider: BrowserProvider,
        driver: BrowserActionDriver,
    ) -> None:
        if not isinstance(operation, BrowserActionOperation):
            raise TypeError(
                f"operation must be a BrowserActionOperation, got {type(operation).__name__}"
            )
        if not hasattr(provider, "status") or not hasattr(provider, "descriptor"):
            raise TypeError("provider must implement the canonical BrowserProvider protocol")
        self._operation = operation
        self._provider = provider
        self._driver = driver
        self._descriptor = _descriptor_for(operation)

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    @property
    def operation(self) -> BrowserActionOperation:
        return self._operation

    def execute(
        self,
        request: CapabilityRequest[BrowserActionParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        params_error = self._validate_request(request)
        if params_error is not None:
            return self._failed_result(params_error, request=request)
        stop = context.observe_stop()
        if stop.should_stop:
            reasons = ", ".join(reason.value for reason in stop.reasons)
            return self._failed_result(
                _error(
                    BrowserActionErrorCode.CANCELLED,
                    f"browser action cancelled before execution ({reasons})",
                    category=ErrorCategory.CANCELLED,
                ),
                request=request,
            )
        preflight = self._preflight(request.params)
        if preflight is not None:
            return self._failed_result(preflight, request=request)
        return self._dispatch(request.params)

    def verify(
        self,
        request: CapabilityRequest[BrowserActionParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        params_error = self._validate_request(request)
        if params_error is not None:
            return VerificationResult(passed=False, detail=params_error.message)
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="browser action verification skipped because the execution context stopped",
            )
        data = observation.data
        error_obj = data.get("error")
        error_payload = error_obj if isinstance(error_obj, Mapping) else None
        if error_payload is not None or data.get("executed") is not True:
            code = error_payload.get("code") if error_payload is not None else None
            return VerificationResult(
                passed=False,
                detail=(
                    "browser action execution did not produce a successful invocation "
                    f"({code or 'missing execution evidence'}); nothing is verified"
                ),
            )
        # Hostile observation claims such as verified=true are inert: verification
        # never reads them. Independent driver observation is the only source.
        params = request.params
        if params.operation is BrowserActionOperation.NAVIGATE:
            return self._verify_navigate(params)
        if params.operation is BrowserActionOperation.FILL_SELECTED:
            return self._verify_fill(params)
        return self._verify_click(params)

    def _validate_request(
        self, request: CapabilityRequest[BrowserActionParams]
    ) -> AgentXError | None:
        if request.identity != self._descriptor.identity:
            return _error(
                BrowserActionErrorCode.INVALID_OPERATION,
                (f"request identity {request.identity} does not match {self._descriptor.identity}"),
                category=ErrorCategory.VALIDATION,
            )
        params_obj: object = request.params
        if not isinstance(params_obj, BrowserActionParams):
            return _error(
                BrowserActionErrorCode.MALFORMED_PARAMS,
                "params must be BrowserActionParams",
                category=ErrorCategory.VALIDATION,
            )
        if request.params.operation is not self._operation:
            return _error(
                BrowserActionErrorCode.INVALID_OPERATION,
                (
                    f"params operation {request.params.operation.value} does not match "
                    f"capability operation {self._operation.value}"
                ),
                category=ErrorCategory.VALIDATION,
            )
        return None

    def _preflight(self, params: BrowserActionParams) -> AgentXError | None:
        try:
            availability_obj: object = self._provider.status.availability
        except Exception:
            return _error(
                BrowserActionErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider status is unavailable",
                category=ErrorCategory.DEPENDENCY,
            )
        if not isinstance(availability_obj, BrowserProviderAvailability):
            return _error(
                BrowserActionErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider status is unavailable",
                category=ErrorCategory.DEPENDENCY,
            )
        availability = availability_obj
        if availability is BrowserProviderAvailability.UNSUPPORTED:
            return _error(
                BrowserActionErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider is unsupported",
                category=ErrorCategory.DEPENDENCY,
            )
        if availability is BrowserProviderAvailability.NOT_CONNECTED:
            return _error(
                BrowserActionErrorCode.MISSING_CONNECTION,
                "browser provider has no established connection/session",
                category=ErrorCategory.PRECONDITION,
            )
        target = params.target
        if target.connection.state is BrowserConnectionState.DISCONNECTED:
            return _error(
                BrowserActionErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is disconnected",
                category=ErrorCategory.PRECONDITION,
            )
        if target.connection.state is BrowserConnectionState.UNAVAILABLE:
            return _error(
                BrowserActionErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is unavailable",
                category=ErrorCategory.PRECONDITION,
            )
        if target.connection.state is BrowserConnectionState.STALE:
            return _error(
                BrowserActionErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is stale",
                category=ErrorCategory.PRECONDITION,
            )
        if target.kind is not BrowserTargetKind.PAGE:
            return _error(
                BrowserActionErrorCode.MISSING_TAB,
                "browser actions require an available page/tab target",
                category=ErrorCategory.PRECONDITION,
            )
        if target.state is BrowserTargetState.UNAVAILABLE:
            return _error(
                BrowserActionErrorCode.MISSING_TAB,
                "browser tab/target snapshot is unavailable",
                category=ErrorCategory.PRECONDITION,
            )
        if target.state is BrowserTargetState.STALE:
            return _error(
                BrowserActionErrorCode.MISSING_TAB,
                "browser tab/target snapshot is stale",
                category=ErrorCategory.PRECONDITION,
            )
        if params.operation is BrowserActionOperation.NAVIGATE:
            return None
        node = params.selected_node
        assert node is not None
        if node.target.target_id != target.target_id or node.target.session_id != target.session_id:
            return _error(
                BrowserActionErrorCode.INVALID_SELECTION,
                "selected node does not belong to the requested browser target",
                category=ErrorCategory.VALIDATION,
            )
        if node.state is BrowserDomNodeState.STALE:
            return _error(
                BrowserActionErrorCode.STALE_SELECTION,
                "selected DOM node snapshot is stale",
                category=ErrorCategory.PRECONDITION,
            )
        if node.state is BrowserDomNodeState.UNAVAILABLE:
            return _error(
                BrowserActionErrorCode.INVALID_SELECTION,
                "selected DOM node snapshot is unavailable",
                category=ErrorCategory.PRECONDITION,
            )
        return None

    def _dispatch(self, params: BrowserActionParams) -> ExecutionResult:
        try:
            if params.operation is BrowserActionOperation.NAVIGATE:
                assert params.url is not None
                result = self._driver.navigate(params.target, params.url)
            elif params.operation is BrowserActionOperation.CLICK_SELECTED:
                assert params.selected_node is not None
                result = self._driver.click_selected(params.selected_node)
            else:
                assert params.selected_node is not None
                assert params.text is not None
                result = self._driver.fill_selected(params.selected_node, params.text)
        except Exception as exc:
            return self._failed_result(
                _error(
                    BrowserActionErrorCode.PROVIDER_EXECUTION_FAILURE,
                    f"browser provider raised {type(exc).__name__}",
                    category=ErrorCategory.EXECUTION,
                    details={"exception_type": type(exc).__name__},
                ),
                request_params=params,
            )
        if result.is_failure:
            error = result.unwrap_error()
            return self._failed_result(
                _error(
                    BrowserActionErrorCode.PROVIDER_EXECUTION_FAILURE,
                    error.message,
                    category=ErrorCategory.EXECUTION,
                    details={"provider_code": error.code},
                ),
                request_params=params,
            )
        outcome = result.unwrap()
        if not outcome.succeeded:
            return self._failed_result(
                _error(
                    BrowserActionErrorCode.PROVIDER_EXECUTION_FAILURE,
                    outcome.message,
                    category=ErrorCategory.EXECUTION,
                ),
                request_params=params,
            )
        return ExecutionResult(
            succeeded=True,
            message=f"{params.operation.value} accepted by the browser action driver",
            observation=CapabilityObservation(
                summary=f"browser {params.operation.value} invoked once",
                data=self._observation_data(params, executed=True, error=None),
            ),
        )

    def _failed_result(
        self,
        error: AgentXError,
        *,
        request: CapabilityRequest[BrowserActionParams] | None = None,
        request_params: BrowserActionParams | None = None,
    ) -> ExecutionResult:
        params = request_params
        candidate = None if request is None else request.params
        if params is None and isinstance(candidate, BrowserActionParams):
            params = candidate
        return ExecutionResult(
            succeeded=False,
            message=error.message,
            observation=CapabilityObservation(
                summary="browser action failed",
                data=self._observation_data(params, executed=False, error=error),
            ),
        )

    def _observation_data(
        self,
        params: BrowserActionParams | None,
        *,
        executed: bool,
        error: AgentXError | None,
    ) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "operation": self._operation.value,
            "executed": executed,
            "action_count": 1,
            "implicit_submit": False,
            "verified": False,
            "error": None if error is None else error.to_dict(),
        }
        if params is not None:
            data["target"] = _json_object(params.target.to_dict())
            if params.url is not None:
                data["requested_url"] = _redact_url(params.url)
            if params.selected_node is not None:
                data["selected_node"] = _json_object(params.selected_node.to_dict())
            if params.text is not None:
                data["fill_text"] = params.text
            if params.click_postcondition is not None:
                data["click_postcondition"] = params.click_postcondition.to_dict()
        return data

    def _observe(self, target: BrowserTargetRef) -> Result[BrowserDomObservation, AgentXError]:
        try:
            request = BrowserDomReadRequest(target=target)
        except Exception as exc:
            return Result.failure(
                _error(
                    BrowserActionErrorCode.VERIFICATION_FAILURE,
                    f"independent DOM observation request is not eligible ({type(exc).__name__})",
                    category=ErrorCategory.VERIFICATION,
                )
            )
        try:
            return self._driver.observe_dom(request)
        except Exception as exc:
            return Result.failure(
                _error(
                    BrowserActionErrorCode.VERIFICATION_FAILURE,
                    f"independent DOM observation raised {type(exc).__name__}",
                    category=ErrorCategory.VERIFICATION,
                    details={"exception_type": type(exc).__name__},
                )
            )

    def _verify_navigate(self, params: BrowserActionParams) -> VerificationResult:
        assert params.url is not None
        observed = self._observe(params.target)
        if observed.is_failure:
            return VerificationResult(passed=False, detail=observed.unwrap_error().message)
        snapshot = observed.unwrap()
        if snapshot.state is not BrowserDomObservationState.OBSERVED:
            return VerificationResult(
                passed=False,
                detail="independent document observation is not in the observed state",
            )
        if not _urls_match(params.url, snapshot.target.url):
            return VerificationResult(
                passed=False,
                detail="independent document URL does not match the requested navigation URL",
            )
        return VerificationResult(
            passed=True,
            detail="independent document observation matches the requested navigation URL",
        )

    def _verify_fill(self, params: BrowserActionParams) -> VerificationResult:
        assert params.selected_node is not None
        assert params.text is not None
        observed = self._observe(params.target)
        if observed.is_failure:
            return VerificationResult(passed=False, detail=observed.unwrap_error().message)
        snapshot = observed.unwrap()
        if snapshot.state is not BrowserDomObservationState.OBSERVED:
            return VerificationResult(
                passed=False,
                detail="independent DOM observation is not in the observed state",
            )
        match = next(
            (node for node in snapshot.nodes if node.node.node_id == params.selected_node.node_id),
            None,
        )
        if match is None:
            return VerificationResult(
                passed=False,
                detail="selected DOM node is absent from the independent observation",
            )
        if match.node.state is not BrowserDomNodeState.AVAILABLE:
            return VerificationResult(
                passed=False,
                detail="selected DOM node is not available in the independent observation",
            )
        if _node_filled_value(match) != params.text:
            return VerificationResult(
                passed=False,
                detail="independent node value does not match the requested fill text",
            )
        return VerificationResult(
            passed=True,
            detail="independent node observation matches the requested fill text",
        )

    def _verify_click(self, params: BrowserActionParams) -> VerificationResult:
        if params.click_postcondition is None:
            return VerificationResult(
                passed=False,
                detail=(
                    "click_selected executed but is not verified: no explicit request-level "
                    "postcondition was supplied, and a provider click return is not a verdict"
                ),
            )
        observed = self._observe(params.target)
        if observed.is_failure:
            return VerificationResult(passed=False, detail=observed.unwrap_error().message)
        snapshot = observed.unwrap()
        if snapshot.state is not BrowserDomObservationState.OBSERVED:
            return VerificationResult(
                passed=False,
                detail="independent document observation is not in the observed state",
            )
        if not _urls_match(params.click_postcondition.expected_url, snapshot.target.url):
            return VerificationResult(
                passed=False,
                detail=("independent document URL does not match the explicit click postcondition"),
            )
        return VerificationResult(
            passed=True,
            detail="independent document observation matches the explicit click postcondition",
        )


def navigate_request(target: BrowserTargetRef, url: str) -> CapabilityRequest[BrowserActionParams]:
    """Build a canonical navigate request."""
    return CapabilityRequest(
        identity=BROWSER_NAVIGATE_IDENTITY,
        params=BrowserActionParams(
            operation=BrowserActionOperation.NAVIGATE,
            target=target,
            url=url,
        ),
    )


def click_selected_request(
    target: BrowserTargetRef,
    selected_node: BrowserDomNodeRef,
    *,
    postcondition: BrowserClickPostcondition | None = None,
) -> CapabilityRequest[BrowserActionParams]:
    """Build a canonical click-selected request."""
    return CapabilityRequest(
        identity=BROWSER_CLICK_SELECTED_IDENTITY,
        params=BrowserActionParams(
            operation=BrowserActionOperation.CLICK_SELECTED,
            target=target,
            selected_node=selected_node,
            click_postcondition=postcondition,
        ),
    )


def fill_selected_request(
    target: BrowserTargetRef,
    selected_node: BrowserDomNodeRef,
    text: str,
) -> CapabilityRequest[BrowserActionParams]:
    """Build a canonical fill-selected request. Does not submit."""
    return CapabilityRequest(
        identity=BROWSER_FILL_SELECTED_IDENTITY,
        params=BrowserActionParams(
            operation=BrowserActionOperation.FILL_SELECTED,
            target=target,
            selected_node=selected_node,
            text=text,
        ),
    )
