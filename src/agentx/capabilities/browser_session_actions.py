"""Governed browser session, cookie, window, and download operations for M7.

These operations remain ordinary canonical capabilities. They neither discover
credentials nor create browser authority. Cookie values and local paths are
sensitive data and are redacted from serialized parameters and observations.
A download is verified by an independently observed artifact digest/size; a
cookie mutation is verified by a fresh cookie read; and window switching is
verified by a fresh active-target observation.

No operation evaluates page JavaScript or accepts executable browser commands.
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
from agentx.capabilities.browser_actions import BrowserActionOutcome
from agentx.capabilities.browser_connection import BrowserTargetRef
from agentx.capabilities.browser_dom import BrowserDomNodeRef, BrowserDomNodeState
from agentx.capabilities.browser_provider import BrowserProvider, BrowserProviderAvailability
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "BrowserCookie",
    "BrowserDownloadObservation",
    "BrowserSessionDriver",
    "BrowserSessionOperation",
    "BrowserSessionParams",
    "BrowserSessionCapability",
    "delete_cookie_request",
    "download_selected_request",
    "set_cookie_request",
    "switch_window_request",
]

_MAX_NAME_LENGTH: Final[int] = 1024
_MAX_VALUE_LENGTH: Final[int] = 65_536
_MAX_FILENAME_LENGTH: Final[int] = 1024
_RESOURCE_ESTIMATE: Final[ResourceEstimate] = ResourceEstimate(
    wall_clock=timedelta(seconds=15),
    machine_actions=1,
    external_cost=Decimal("0"),
)


class BrowserSessionOperation(StrEnum):
    DOWNLOAD_SELECTED = "download_selected"
    SET_COOKIE = "set_cookie"
    DELETE_COOKIE = "delete_cookie"
    SWITCH_WINDOW = "switch_window"


@dataclass(frozen=True, slots=True)
class BrowserCookie:
    """Explicit cookie mutation contract; the value is sensitive inert data."""

    name: str
    value: str = field(repr=False)
    path: str = "/"
    secure: bool = False
    http_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or self.name != self.name.strip():
            raise ValueError("cookie name must be non-empty and trimmed")
        if len(self.name) > _MAX_NAME_LENGTH or any(ch in self.name for ch in "\x00\r\n;"):
            raise ValueError("cookie name is invalid or exceeds the bounded length")
        if not isinstance(self.value, str):
            raise TypeError("cookie value must be a string")
        if len(self.value) > _MAX_VALUE_LENGTH or "\x00" in self.value:
            raise ValueError("cookie value is invalid or exceeds the bounded length")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError("cookie path must be an absolute URL path")
        if type(self.secure) is not bool or type(self.http_only) is not bool:
            raise TypeError("cookie secure/http_only must be bool values")

    def to_driver_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
        }

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "value": "[REDACTED]",
            "path": self.path,
            "secure": self.secure,
            "http_only": self.http_only,
        }


@dataclass(frozen=True, slots=True)
class BrowserDownloadObservation:
    """Independent local artifact evidence; content is never embedded."""

    filename: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.filename, str)
            or not self.filename
            or PurePath(self.filename).name != self.filename
        ):
            raise ValueError("filename must be one safe basename")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative int")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class BrowserSessionParams(CapabilityParams):
    operation: BrowserSessionOperation
    target: BrowserTargetRef
    selected_node: BrowserDomNodeRef | None = None
    expected_filename: str | None = None
    cookie: BrowserCookie | None = field(default=None, repr=False)
    cookie_name: str | None = None
    window_handle: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, BrowserSessionOperation):
            raise TypeError("operation must be BrowserSessionOperation")
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError("target must be BrowserTargetRef")

        fields = {
            "selected_node": self.selected_node,
            "expected_filename": self.expected_filename,
            "cookie": self.cookie,
            "cookie_name": self.cookie_name,
            "window_handle": self.window_handle,
        }
        required: dict[BrowserSessionOperation, tuple[str, ...]] = {
            BrowserSessionOperation.DOWNLOAD_SELECTED: (
                "selected_node",
                "expected_filename",
            ),
            BrowserSessionOperation.SET_COOKIE: ("cookie",),
            BrowserSessionOperation.DELETE_COOKIE: ("cookie_name",),
            BrowserSessionOperation.SWITCH_WINDOW: ("window_handle",),
        }
        allowed = set(required[self.operation])
        for name, value in fields.items():
            if name in allowed and value is None:
                raise ValueError(f"{self.operation.value} requires {name}")
            if name not in allowed and value is not None:
                raise ValueError(f"{self.operation.value} rejects unrelated field {name}")

        if self.selected_node is not None:
            if not isinstance(self.selected_node, BrowserDomNodeRef):
                raise TypeError("selected_node must be BrowserDomNodeRef")
            if self.selected_node.target.target_id != self.target.target_id:
                raise ValueError("selected_node must belong to the exact target")
        if self.expected_filename is not None:
            if (
                not isinstance(self.expected_filename, str)
                or not self.expected_filename
                or PurePath(self.expected_filename).name != self.expected_filename
                or len(self.expected_filename) > _MAX_FILENAME_LENGTH
            ):
                raise ValueError("expected_filename must be one bounded basename")
        if self.cookie is not None and not isinstance(self.cookie, BrowserCookie):
            raise TypeError("cookie must be BrowserCookie")
        if self.cookie_name is not None:
            if (
                not isinstance(self.cookie_name, str)
                or not self.cookie_name
                or self.cookie_name != self.cookie_name.strip()
            ):
                raise ValueError("cookie_name must be non-empty and trimmed")
        if self.window_handle is not None:
            if (
                not isinstance(self.window_handle, str)
                or not self.window_handle
                or self.window_handle != self.window_handle.strip()
            ):
                raise ValueError("window_handle must be non-empty and trimmed")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": self.operation.value,
            "target": self.target.to_dict(),
            "selected_node": (
                None if self.selected_node is None else self.selected_node.to_dict()
            ),
            "expected_filename": self.expected_filename,
            "cookie": None if self.cookie is None else self.cookie.to_dict(),
            "cookie_name": self.cookie_name,
            "window_handle": self.window_handle,
            "action_count": 1,
        }


class BrowserSessionDriver(Protocol):
    def download_selected(
        self, node: BrowserDomNodeRef, expected_filename: str
    ) -> Result[BrowserActionOutcome, AgentXError]: ...

    def observe_download(
        self, expected_filename: str
    ) -> Result[BrowserDownloadObservation, AgentXError]: ...

    def cookies(self) -> Result[tuple[dict[str, object], ...], AgentXError]: ...

    def set_cookie(self, cookie: dict[str, object]) -> Result[None, AgentXError]: ...

    def delete_cookie(self, name: str) -> Result[None, AgentXError]: ...

    def switch_window(self, handle: str) -> Result[BrowserTargetRef, AgentXError]: ...

    def target_ref(self) -> Result[BrowserTargetRef, AgentXError]: ...


def _identity(operation: BrowserSessionOperation) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName(f"browser.session.{operation.value}"),
        version=CapabilityVersion(1, 0, 0),
    )


def _descriptor(operation: BrowserSessionOperation) -> CapabilityDescriptor:
    if operation is BrowserSessionOperation.SWITCH_WINDOW:
        permissions = frozenset({Permission.WRITE})
        external = False
    elif operation is BrowserSessionOperation.DOWNLOAD_SELECTED:
        permissions = frozenset({Permission.READ, Permission.EXTERNAL_EFFECT})
        external = True
    else:
        permissions = frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT})
        external = True
    return CapabilityDescriptor(
        identity=_identity(operation),
        description=f"Perform one explicit governed browser-session operation: {operation.value}.",
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=permissions,
        risk_assessment=assess_risk(
            read_only=operation is BrowserSessionOperation.DOWNLOAD_SELECTED,
            modifies_state=True,
            reversible=False,
            external_effect=external,
        ),
        preconditions=(),
        rollback=RollbackDeclaration(
            support=RollbackSupport.NOT_APPLICABLE,
            detail="Browser session operations are not assumed reversible.",
        ),
        estimate=_RESOURCE_ESTIMATE,
    )


def _error(code: str, message: str, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
    )


class BrowserSessionCapability:
    __slots__ = ("_descriptor", "_driver", "_operation", "_provider")

    def __init__(
        self,
        *,
        operation: BrowserSessionOperation,
        provider: BrowserProvider,
        driver: BrowserSessionDriver,
    ) -> None:
        if not isinstance(operation, BrowserSessionOperation):
            raise TypeError("operation must be BrowserSessionOperation")
        self._operation = operation
        self._provider = provider
        self._driver = driver
        self._descriptor = _descriptor(operation)

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[BrowserSessionParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        params = request.params
        if request.identity != self._descriptor.identity or not isinstance(
            params, BrowserSessionParams
        ):
            return self._failed("browser.session.invalid_request", "invalid session request", None)
        if params.operation is not self._operation:
            return self._failed(
                "browser.session.invalid_operation",
                "session operation does not match capability",
                params,
            )
        if context.observe_stop().should_stop:
            return self._failed("browser.session.cancelled", "execution context stopped", params)
        if self._provider.status.availability is not BrowserProviderAvailability.AVAILABLE:
            return self._failed(
                "browser.session.provider_unavailable",
                "browser provider is unavailable",
                params,
            )
        if (
            params.selected_node is not None
            and params.selected_node.state is not BrowserDomNodeState.AVAILABLE
        ):
            return self._failed(
                "browser.session.stale_selection",
                "selected browser node is stale or unavailable",
                params,
            )
        try:
            if params.operation is BrowserSessionOperation.DOWNLOAD_SELECTED:
                assert params.selected_node is not None
                assert params.expected_filename is not None
                result = self._driver.download_selected(
                    params.selected_node, params.expected_filename
                )
                if result.is_failure or not result.unwrap().succeeded:
                    return self._failed(
                        "browser.session.download_failed",
                        "browser download invocation failed",
                        params,
                    )
            elif params.operation is BrowserSessionOperation.SET_COOKIE:
                assert params.cookie is not None
                result = self._driver.set_cookie(params.cookie.to_driver_dict())
                if result.is_failure:
                    return self._failed(
                        "browser.session.cookie_write_failed",
                        "browser cookie mutation failed",
                        params,
                    )
            elif params.operation is BrowserSessionOperation.DELETE_COOKIE:
                assert params.cookie_name is not None
                result = self._driver.delete_cookie(params.cookie_name)
                if result.is_failure:
                    return self._failed(
                        "browser.session.cookie_delete_failed",
                        "browser cookie deletion failed",
                        params,
                    )
            else:
                assert params.window_handle is not None
                result = self._driver.switch_window(params.window_handle)
                if result.is_failure:
                    return self._failed(
                        "browser.session.window_switch_failed",
                        "browser window switch failed",
                        params,
                    )
        except Exception as exc:
            return self._failed(
                "browser.session.driver_failure",
                f"browser session driver raised {type(exc).__name__}",
                params,
            )
        return ExecutionResult(
            succeeded=True,
            message=f"{params.operation.value} accepted by browser session driver",
            observation=CapabilityObservation(
                summary=f"browser {params.operation.value} invoked once",
                data={**params.to_dict(), "executed": True, "verified": False},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[BrowserSessionParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        params = request.params
        if not isinstance(params, BrowserSessionParams):
            return VerificationResult(passed=False, detail="invalid session parameters")
        if context.observe_stop().should_stop or observation.data.get("executed") is not True:
            return VerificationResult(passed=False, detail="session operation is not executable")
        try:
            if params.operation is BrowserSessionOperation.DOWNLOAD_SELECTED:
                assert params.expected_filename is not None
                result = self._driver.observe_download(params.expected_filename)
                if result.is_failure:
                    return VerificationResult(
                        passed=False,
                        detail="independent downloaded artifact was not observed",
                    )
                artifact = result.unwrap()
                passed = artifact.filename == params.expected_filename and artifact.size_bytes > 0
                return VerificationResult(
                    passed=passed,
                    detail=(
                        "independent downloaded artifact exists with content"
                        if passed
                        else "download artifact verification mismatch"
                    ),
                )
            if params.operation in {
                BrowserSessionOperation.SET_COOKIE,
                BrowserSessionOperation.DELETE_COOKIE,
            }:
                result = self._driver.cookies()
                if result.is_failure:
                    return VerificationResult(
                        passed=False,
                        detail="independent cookie observation failed",
                    )
                cookies = result.unwrap()
                if params.operation is BrowserSessionOperation.SET_COOKIE:
                    assert params.cookie is not None
                    match = next(
                        (
                            item
                            for item in cookies
                            if item.get("name") == params.cookie.name
                        ),
                        None,
                    )
                    passed = match is not None and match.get("value") == params.cookie.value
                else:
                    assert params.cookie_name is not None
                    passed = all(
                        item.get("name") != params.cookie_name for item in cookies
                    )
                return VerificationResult(
                    passed=passed,
                    detail=(
                        "independent cookie state matches request"
                        if passed
                        else "cookie state verification mismatch"
                    ),
                )
            assert params.window_handle is not None
            target = self._driver.target_ref()
            passed = (
                target.is_success
                and target.unwrap().target_id.value == params.window_handle
            )
            return VerificationResult(
                passed=passed,
                detail=(
                    "independent active-window observation matches request"
                    if passed
                    else "active-window verification mismatch"
                ),
            )
        except Exception:
            return VerificationResult(
                passed=False,
                detail="independent browser-session verification failed closed",
            )

    def _failed(
        self,
        code: str,
        message: str,
        params: BrowserSessionParams | None,
    ) -> ExecutionResult:
        error = _error(code, message, ErrorCategory.EXECUTION)
        data: dict[str, JsonValue] = {
            "operation": self._operation.value,
            "executed": False,
            "verified": False,
            "error": error.to_dict(),
        }
        if params is not None:
            data.update(params.to_dict())
        return ExecutionResult(
            succeeded=False,
            message=message,
            observation=CapabilityObservation(
                summary="browser session operation failed",
                data=data,
            ),
        )


def download_selected_request(
    target: BrowserTargetRef,
    selected_node: BrowserDomNodeRef,
    expected_filename: str,
) -> CapabilityRequest[BrowserSessionParams]:
    operation = BrowserSessionOperation.DOWNLOAD_SELECTED
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserSessionParams(
            operation=operation,
            target=target,
            selected_node=selected_node,
            expected_filename=expected_filename,
        ),
    )


def set_cookie_request(
    target: BrowserTargetRef,
    cookie: BrowserCookie,
) -> CapabilityRequest[BrowserSessionParams]:
    operation = BrowserSessionOperation.SET_COOKIE
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserSessionParams(
            operation=operation,
            target=target,
            cookie=cookie,
        ),
    )


def delete_cookie_request(
    target: BrowserTargetRef,
    cookie_name: str,
) -> CapabilityRequest[BrowserSessionParams]:
    operation = BrowserSessionOperation.DELETE_COOKIE
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserSessionParams(
            operation=operation,
            target=target,
            cookie_name=cookie_name,
        ),
    )


def switch_window_request(
    target: BrowserTargetRef,
    window_handle: str,
) -> CapabilityRequest[BrowserSessionParams]:
    operation = BrowserSessionOperation.SWITCH_WINDOW
    return CapabilityRequest(
        identity=_identity(operation),
        params=BrowserSessionParams(
            operation=operation,
            target=target,
            window_handle=window_handle,
        ),
    )
