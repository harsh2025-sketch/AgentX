"""N2.26 governed browser navigation capability.

This module is the standalone, governed **URL navigation** boundary. It owns
exactly one explicit operation:

* ``navigate_to_url`` — navigate one already-identified C5.02 page/tab target
  to one explicit absolute ``http``/``https`` URL.

It is deliberately distinct from the M7.01 governed browser action module
(``browser_actions.py``), which owns multi-operation *actions* on already
selected nodes and is not modified here, and from any browser form/text-entry
worker (N2.27), which this module does not depend on.

Scope
-----

One capability invocation performs **one** governed navigation operation and
nothing else. This module never clicks, types, submits, accepts dialogs,
downloads, uploads, follows a redirect manually, evaluates JavaScript,
executes CDP/devtools passthrough, calls a model to decide a follow-up action,
mutates Task state, persists anything, or marks Task success.

The canonical provider/adapter boundary is reused, not rebuilt:

* C5.01 :class:`~agentx.capabilities.browser_provider.BrowserProvider`
  descriptive availability is the preflight fact;
* C5.02 :class:`~agentx.capabilities.browser_connection.BrowserTargetRef`
  carries the already-established connection/session/tab identity;
* the injected :class:`BrowserNavigationDriver` is the narrow, already-bound
  action port (navigation plus the independent C5.03 DOM read used for
  verification), mirroring the M7.01 driver/adapter convention.

URL policy
----------

Validation is fail-closed: explicit absolute URL, ``http``/``https`` only,
bounded length, no control characters/whitespace, explicit host, and every
dangerous scheme (``javascript:``, ``data:``, ``file:``, ``shell:``,
``powershell:`` and the rest) is rejected. Userinfo is redacted from every
projection so credentials cannot leak through evidence.

External-effect semantics
-------------------------

Navigation is **not** classified as read-only because the method is named
``navigate``. Loading a URL can fetch remote content, transmit request
metadata, alter browser state, and trigger server-side effects. The descriptor
therefore requires ``WRITE`` + ``EXTERNAL_EFFECT`` and classifies the operation
as R3 through the canonical ``assess_risk(external_effect=True, ...)`` path.
The capability itself never invokes the kernel authority path: the action
gate, the permission engine, the emergency stop, and the resource budget stay
inside the canonical closed-loop execution path, which remains the only
authority that can allow or deny a run.

Verification and redirects
--------------------------

A driver ``succeeded=True`` return is observation evidence only and never a
verdict. ``verify`` independently re-observes the target's current document
URL through the canonical C5.03 read request. Redirect semantics are explicit:

* provider-supplied final/current URL evidence is preserved and distinguished
  from the requested URL;
* no redirect information, or a final URL equal to the requested URL, means the
  independent observation must equal the requested URL;
* a same-origin final URL (equal scheme/host/port) is modeled as a same-site
  redirect landing page: verification passes only when the independent
  observation independently confirms that final URL;
* a **cross-origin** final URL is never treated as verified success: the
  requested and observed final URLs are preserved as evidence and verification
  fails closed.

Hostile page content (``permission=ADMIN``, ``verified=true``, instructions
such as ``click this next`` or ``send credentials``) is untrusted inert data.
It cannot change permission, risk, budget, the emergency-stop state, identity,
verification, or any follow-up action, and this module never acts on it.

Owner: N2.26. Belongs to ``agentx.capabilities``. Imports only the standard
library, canonical ``agentx.core`` / ``agentx.kernel`` contracts, the
capability ABI, and the existing C5.01-C5.03 browser identity/observation
contracts. It does not import ``browser_actions.py`` or any outer adaptive
subsystem.
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
    "BROWSER_NAVIGATE_TO_URL_IDENTITY",
    "MAX_NAVIGATION_URL_LENGTH",
    "SUPPORTED_NAVIGATION_SCHEMES",
    "BrowserNavigationCapability",
    "BrowserNavigationDriver",
    "BrowserNavigationErrorCode",
    "BrowserNavigationOperation",
    "BrowserNavigationOutcome",
    "BrowserNavigationParams",
    "BrowserNavigationValidationError",
    "navigate_to_url_request",
]

BROWSER_NAVIGATE_TO_URL_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("browser.navigation.navigate_to_url"),
    version=CapabilityVersion(1, 0, 0),
)

MAX_NAVIGATION_URL_LENGTH: Final[int] = 8192
SUPPORTED_NAVIGATION_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_FORBIDDEN_NAVIGATION_SCHEMES: Final[frozenset[str]] = frozenset(
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


class BrowserNavigationValidationError(ValueError):
    """Raised when a browser-navigation contract value is malformed."""


class BrowserNavigationErrorCode(StrEnum):
    """Stable operational error codes for the navigation capability."""

    INVALID_OPERATION = "browser.navigation.invalid_operation"
    INVALID_URL = "browser.navigation.invalid_url"
    UNSUPPORTED_SCHEME = "browser.navigation.unsupported_scheme"
    MISSING_CONNECTION = "browser.navigation.missing_connection"
    MISSING_TAB = "browser.navigation.missing_tab"
    PROVIDER_UNAVAILABLE = "browser.navigation.provider_unavailable"
    PROVIDER_EXECUTION_FAILURE = "browser.navigation.provider_execution_failure"
    VERIFICATION_FAILURE = "browser.navigation.verification_failure"
    CANCELLED = "browser.navigation.cancelled"
    MALFORMED_PARAMS = "browser.navigation.malformed_params"


class BrowserNavigationOperation(StrEnum):
    """Closed vocabulary of governed browser navigation operations.

    Exactly one member exists: a navigation capability invocation performs one
    governed ``navigate_to_url`` operation and nothing else.
    """

    NAVIGATE_TO_URL = "navigate_to_url"


def _json_object(value: object) -> dict[str, JsonValue]:
    """Return a JSON-object mapping, failing closed on unexpected shapes."""
    if not isinstance(value, dict):
        raise BrowserNavigationValidationError("expected a JSON object")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise BrowserNavigationValidationError(
        f"non-JSON-compatible value of type {type(value).__name__}"
    )


def _redact_url(url: str) -> str:
    """Strip userinfo from a URL so credentials cannot leak through errors."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _classify_url(
    value: object,
) -> tuple[str | None, BrowserNavigationErrorCode | None, str | None]:
    """Validate an explicit navigation URL. Returns ``(url, code, message)``."""
    if not isinstance(value, str):
        return None, BrowserNavigationErrorCode.MALFORMED_PARAMS, "url must be a string"
    if not value or value != value.strip():
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            "url must be non-empty and trimmed",
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            "url must not contain control characters",
        )
    if any(character.isspace() for character in value):
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            "url must not contain whitespace",
        )
    if len(value) > MAX_NAVIGATION_URL_LENGTH:
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            f"url must not exceed {MAX_NAVIGATION_URL_LENGTH} characters",
        )
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if not scheme:
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            "url must include an explicit scheme",
        )
    if scheme in _FORBIDDEN_NAVIGATION_SCHEMES or scheme not in SUPPORTED_NAVIGATION_SCHEMES:
        return (
            None,
            BrowserNavigationErrorCode.UNSUPPORTED_SCHEME,
            f"url scheme {scheme!r} is not supported; only http and https are allowed",
        )
    if not parts.netloc or not (parts.hostname or "").strip():
        return (
            None,
            BrowserNavigationErrorCode.INVALID_URL,
            "url must include an explicit host",
        )
    return value, None, None


def _require_url(value: object) -> str:
    url, code, message = _classify_url(value)
    if url is None:
        assert message is not None
        if code is BrowserNavigationErrorCode.UNSUPPORTED_SCHEME:
            raise BrowserNavigationValidationError(message)
        raise BrowserNavigationValidationError(f"url: {message}")
    return url


def _url_parts(url: str) -> tuple[str, str | None, int | None]:
    """Return ``(lowercase scheme, lowercase hostname, explicit port)``."""
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def _urls_equal(expected: str, observed: str | None) -> bool:
    """Canonical URL-equality check mirroring the browser-action convention."""
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


def _same_origin(left: str, right: str) -> bool:
    """Whether two http(s) URLs share scheme, host, and explicit port."""
    return _url_parts(left) == _url_parts(right)


def _brief_url(url: str) -> str:
    """Truncate a URL for bounded verification detail text (never for evidence)."""
    redacted = _redact_url(url)
    if len(redacted) <= 160:
        return redacted
    return f"{redacted[:157]}..."


def _error(
    code: BrowserNavigationErrorCode,
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
class BrowserNavigationParams(CapabilityParams):
    """Typed parameters for exactly one governed navigation operation.

    Only an explicit absolute ``http``/``https`` URL and one canonical C5.02
    page/tab target are accepted. Natural language, search-engine queries,
    relative URLs, navigation modes, and any extra action fields are rejected.
    """

    target: BrowserTargetRef
    url: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError(f"target must be a BrowserTargetRef, got {type(self.target).__name__}")
        object.__setattr__(self, "url", _require_url(self.url))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": BrowserNavigationOperation.NAVIGATE_TO_URL.value,
            "target": _json_object(self.target.to_dict()),
            "url": _redact_url(self.url),
            "action_count": 1,
        }


@dataclass(frozen=True, slots=True)
class BrowserNavigationOutcome:
    """Driver-supplied navigation evidence. Never a verification verdict.

    ``final_url`` is the provider-observed final/current URL after the
    navigation settled (for example after a redirect). Absence means the
    provider did not supply one. The field is inert evidence: it is validated
    and preserved, and it never verifies anything by itself.
    """

    succeeded: bool
    message: str
    final_url: str | None = None

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise TypeError(f"succeeded must be bool, got {type(self.succeeded).__name__}")
        if (
            not isinstance(self.message, str)
            or not self.message
            or self.message != self.message.strip()
        ):
            raise BrowserNavigationValidationError(
                "outcome message must be a non-empty trimmed string"
            )
        if self.final_url is not None and not isinstance(self.final_url, str):
            raise TypeError(
                f"final_url must be a string or None, got {type(self.final_url).__name__}"
            )


class BrowserNavigationDriver(Protocol):
    """Narrow already-connected navigation/observation port.

    Implementations must not expose JavaScript evaluation, CDP passthrough,
    process launch, downloads, uploads, popup acceptance, or multi-action
    chaining. ``navigate`` performs one already-validated http(s) navigation;
    ``observe_dom`` is the independent canonical C5.03 verification read and is
    not authorization.
    """

    def navigate(
        self,
        target: BrowserTargetRef,
        url: str,
    ) -> Result[BrowserNavigationOutcome, AgentXError]:
        """Navigate ``target`` to an already-validated http(s) URL exactly once."""
        ...

    def observe_dom(
        self,
        request: BrowserDomReadRequest,
    ) -> Result[BrowserDomObservation, AgentXError]:
        """Return one independent structured document/current-URL snapshot."""
        ...


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=BROWSER_NAVIGATE_TO_URL_IDENTITY,
        description=(
            "Navigate one already-identified browser page/tab to an explicit "
            "http or https URL. Navigation is an external observable action: "
            "it can load remote content, transmit request metadata, alter "
            "browser state, and trigger server-side effects. A driver return "
            "is evidence only and never verification."
        ),
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
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
                "Browser navigation is not rolled back by this capability; a navigation "
                "is treated as irreversible at this boundary."
            ),
        ),
        estimate=_RESOURCE_ESTIMATE,
    )


class BrowserNavigationCapability:
    """One governed URL navigation as a canonical Capability.

    The descriptor is fixed at construction. Page content, page titles, and
    URLs are untrusted data: none of them can change permission, risk,
    identity, verification, or budget. The CapabilityExecutionLoop remains the
    only authority path; this object never invokes kernel gating, never mutates
    Task state, and never marks Task success.
    """

    __slots__ = ("_descriptor", "_driver", "_provider")

    def __init__(
        self,
        *,
        provider: BrowserProvider,
        driver: BrowserNavigationDriver,
    ) -> None:
        if not hasattr(provider, "status") or not hasattr(provider, "descriptor"):
            raise TypeError("provider must implement the canonical BrowserProvider protocol")
        self._provider = provider
        self._driver = driver
        self._descriptor = _descriptor()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[BrowserNavigationParams],
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
                    BrowserNavigationErrorCode.CANCELLED,
                    f"browser navigation cancelled before execution ({reasons})",
                    category=ErrorCategory.CANCELLED,
                ),
                request=request,
            )
        preflight = self._preflight(request.params)
        if preflight is not None:
            return self._failed_result(preflight, request=request)
        params = request.params
        try:
            result = self._driver.navigate(params.target, params.url)
        except Exception as exc:
            return self._failed_result(
                _error(
                    BrowserNavigationErrorCode.PROVIDER_EXECUTION_FAILURE,
                    f"browser navigation driver raised {type(exc).__name__}",
                    category=ErrorCategory.EXECUTION,
                    details={"exception_type": type(exc).__name__},
                ),
                request=request,
            )
        if result.is_failure:
            error = result.unwrap_error()
            return self._failed_result(
                _error(
                    BrowserNavigationErrorCode.PROVIDER_EXECUTION_FAILURE,
                    error.message,
                    category=ErrorCategory.EXECUTION,
                    details={"provider_code": error.code},
                ),
                request=request,
            )
        outcome = result.unwrap()
        if not outcome.succeeded:
            return self._failed_result(
                _error(
                    BrowserNavigationErrorCode.PROVIDER_EXECUTION_FAILURE,
                    outcome.message,
                    category=ErrorCategory.EXECUTION,
                ),
                request=request,
            )
        return ExecutionResult(
            succeeded=True,
            message="browser navigation to the requested URL was invoked once",
            observation=CapabilityObservation(
                summary="browser navigation invoked once",
                data=self._observation_data(request.params, outcome=outcome, error=None),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[BrowserNavigationParams],
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
                detail=(
                    "browser navigation verification skipped because the execution context stopped"
                ),
            )
        data = observation.data
        error_obj = data.get("error")
        error_payload = error_obj if isinstance(error_obj, Mapping) else None
        if error_payload is not None or data.get("executed") is not True:
            code = error_payload.get("code") if error_payload is not None else None
            return VerificationResult(
                passed=False,
                detail=(
                    "browser navigation execution did not produce a successful invocation "
                    f"({code or 'missing execution evidence'}); nothing is verified"
                ),
            )
        return self._verify_landing(request.params, data)

    def _validate_request(
        self,
        request: CapabilityRequest[BrowserNavigationParams],
    ) -> AgentXError | None:
        if request.identity != self._descriptor.identity:
            return _error(
                BrowserNavigationErrorCode.INVALID_OPERATION,
                (f"request identity {request.identity} does not match {self._descriptor.identity}"),
                category=ErrorCategory.VALIDATION,
            )
        params_obj: object = request.params
        if not isinstance(params_obj, BrowserNavigationParams):
            return _error(
                BrowserNavigationErrorCode.MALFORMED_PARAMS,
                "params must be BrowserNavigationParams",
                category=ErrorCategory.VALIDATION,
            )
        return None

    def _preflight(self, params: BrowserNavigationParams) -> AgentXError | None:
        try:
            availability_obj: object = self._provider.status.availability
        except Exception:
            return _error(
                BrowserNavigationErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider status is unavailable",
                category=ErrorCategory.DEPENDENCY,
            )
        if not isinstance(availability_obj, BrowserProviderAvailability):
            return _error(
                BrowserNavigationErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider status is unavailable",
                category=ErrorCategory.DEPENDENCY,
            )
        availability = availability_obj
        if availability is BrowserProviderAvailability.UNSUPPORTED:
            return _error(
                BrowserNavigationErrorCode.PROVIDER_UNAVAILABLE,
                "browser provider is unsupported",
                category=ErrorCategory.DEPENDENCY,
            )
        if availability is BrowserProviderAvailability.NOT_CONNECTED:
            return _error(
                BrowserNavigationErrorCode.MISSING_CONNECTION,
                "browser provider has no established connection/session",
                category=ErrorCategory.PRECONDITION,
            )
        target = params.target
        if target.connection.state is BrowserConnectionState.DISCONNECTED:
            return _error(
                BrowserNavigationErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is disconnected",
                category=ErrorCategory.PRECONDITION,
            )
        if target.connection.state is BrowserConnectionState.UNAVAILABLE:
            return _error(
                BrowserNavigationErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is unavailable",
                category=ErrorCategory.PRECONDITION,
            )
        if target.connection.state is BrowserConnectionState.STALE:
            return _error(
                BrowserNavigationErrorCode.MISSING_CONNECTION,
                "browser connection snapshot is stale",
                category=ErrorCategory.PRECONDITION,
            )
        if target.kind is not BrowserTargetKind.PAGE:
            return _error(
                BrowserNavigationErrorCode.MISSING_TAB,
                "browser navigation requires an available page/tab target",
                category=ErrorCategory.PRECONDITION,
            )
        if target.state is BrowserTargetState.UNAVAILABLE:
            return _error(
                BrowserNavigationErrorCode.MISSING_TAB,
                "browser tab/target snapshot is unavailable",
                category=ErrorCategory.PRECONDITION,
            )
        if target.state is BrowserTargetState.STALE:
            return _error(
                BrowserNavigationErrorCode.MISSING_TAB,
                "browser tab/target snapshot is stale",
                category=ErrorCategory.PRECONDITION,
            )
        return None

    def _failed_result(
        self,
        error: AgentXError,
        *,
        request: CapabilityRequest[BrowserNavigationParams],
    ) -> ExecutionResult:
        return ExecutionResult(
            succeeded=False,
            message=error.message,
            observation=CapabilityObservation(
                summary="browser navigation failed",
                data=self._observation_data(request.params, outcome=None, error=error),
            ),
        )

    def _observation_data(
        self,
        params: BrowserNavigationParams,
        *,
        outcome: BrowserNavigationOutcome | None,
        error: AgentXError | None,
    ) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "operation": BrowserNavigationOperation.NAVIGATE_TO_URL.value,
            "executed": outcome is not None and outcome.succeeded,
            "action_count": 1,
            "verified": False,
            "error": None if error is None else error.to_dict(),
        }
        if isinstance(params, BrowserNavigationParams):
            data["target"] = _json_object(params.target.to_dict())
            data["requested_url"] = _redact_url(params.url)
        data["provider_final_url"] = None
        data["redirect_observed"] = False
        if outcome is not None:
            final_url = outcome.final_url
            data["provider_final_url"] = None if final_url is None else _redact_url(final_url)
            data["redirect_observed"] = final_url is not None and not _urls_equal(
                params.url, final_url
            )
        return data

    def _observe(self, target: BrowserTargetRef) -> Result[BrowserDomObservation, AgentXError]:
        try:
            request = BrowserDomReadRequest(target=target)
        except Exception as exc:
            return Result.failure(
                _error(
                    BrowserNavigationErrorCode.VERIFICATION_FAILURE,
                    (
                        "independent current-URL observation request is not "
                        f"eligible ({type(exc).__name__})"
                    ),
                    category=ErrorCategory.VERIFICATION,
                )
            )
        try:
            return self._driver.observe_dom(request)
        except Exception as exc:
            return Result.failure(
                _error(
                    BrowserNavigationErrorCode.VERIFICATION_FAILURE,
                    f"independent current-URL observation raised {type(exc).__name__}",
                    category=ErrorCategory.VERIFICATION,
                    details={"exception_type": type(exc).__name__},
                )
            )

    def _verify_landing(
        self,
        params: BrowserNavigationParams,
        data: Mapping[str, object],
    ) -> VerificationResult:
        """Independently verify the landed document URL under the redirect policy.

        Evidence from the invocation is read first (``provider_final_url``),
        then the current document URL is independently re-observed through the
        canonical C5.03 read. The expected landing URL is:

        * the requested URL when the provider reported no final URL or a final
          URL equal to the requested URL;
        * the provider final URL only when it is same-origin with the requested
          URL (an explicit same-site redirect landing model);
        * never a cross-origin URL: an unexpected cross-origin redirect fails
          closed regardless of any evidence.

        Verification passes only when the independent observation matches the
        expected landing URL. A provider return alone never passes.
        """
        requested = params.url
        final_obj = data.get("provider_final_url")
        final_url = final_obj if isinstance(final_obj, str) else None
        if final_url is not None:
            classified, _, message = _classify_url(final_url)
            if classified is None:
                return VerificationResult(
                    passed=False,
                    detail=(
                        f"provider-reported final URL is invalid ({message}); nothing is verified"
                    ),
                )
            if not _urls_equal(requested, final_url) and not _same_origin(requested, final_url):
                return VerificationResult(
                    passed=False,
                    detail=(
                        "navigation landed at an unexpected cross-origin final URL; "
                        "requested URL and final URL differ in origin, and a cross-origin "
                        "redirect is not treated as verified success"
                    ),
                )
        expected = final_url if final_url is not None else requested
        observed = self._observe(params.target)
        if observed.is_failure:
            return VerificationResult(passed=False, detail=observed.unwrap_error().message)
        snapshot = observed.unwrap()
        if snapshot.state is not BrowserDomObservationState.OBSERVED:
            return VerificationResult(
                passed=False,
                detail="independent document observation is not in the observed state",
            )
        current_url = snapshot.target.url
        if not _urls_equal(expected, current_url):
            return VerificationResult(
                passed=False,
                detail=(
                    "independent current-URL observation does not match the expected "
                    "landing URL (requested="
                    f"{_brief_url(requested)}, expected={_brief_url(expected)}, "
                    "observed="
                    f"{'<none>' if current_url is None else _brief_url(current_url)})"
                ),
            )
        if final_url is not None and not _urls_equal(requested, final_url):
            return VerificationResult(
                passed=True,
                detail=(
                    "independent current-URL observation confirms the same-origin "
                    "redirect landing URL reported by the provider"
                ),
            )
        return VerificationResult(
            passed=True,
            detail="independent current-URL observation matches the requested navigation URL",
        )


def navigate_to_url_request(
    target: BrowserTargetRef,
    url: str,
) -> CapabilityRequest[BrowserNavigationParams]:
    """Build a canonical navigate-to-URL request."""
    return CapabilityRequest(
        identity=BROWSER_NAVIGATE_TO_URL_IDENTITY,
        params=BrowserNavigationParams(target=target, url=url),
    )
