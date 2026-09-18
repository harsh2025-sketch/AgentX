"""Concrete zero-third-party W3C WebDriver browser provider for M7.

This adapter speaks the standardized WebDriver HTTP protocol using only the
Python standard library. It exposes no JavaScript execution, CDP passthrough,
shell command channel, or page-supplied executable parameter. The optional
local service launcher only executes the fixed chromedriver program discovered
by the host PATH, with a fixed argument shape and shell=False.

The provider implements the existing narrow browser action driver plus the
explicit form driver. DOM observation is produced through WebDriver element
queries and standard element properties, never through injected page script.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from agentx.capabilities.abi import Capability
from agentx.capabilities.browser_actions import (
    BrowserActionOperation,
    BrowserActionOutcome,
    BrowserActionsCapability,
)
from agentx.capabilities.browser_connection import (
    BrowserConnectionRef,
    BrowserConnectionState,
    BrowserSessionId,
    BrowserTargetId,
    BrowserTargetKind,
    BrowserTargetRef,
    BrowserTargetState,
)
from agentx.capabilities.browser_dom import (
    BrowserDomAttribute,
    BrowserDomNodeId,
    BrowserDomNodeRef,
    BrowserDomNodeSnapshot,
    BrowserDomNodeState,
    BrowserDomObservation,
    BrowserDomObservationState,
    BrowserDomReadRequest,
)
from agentx.capabilities.browser_forms import (
    BrowserFormOperation,
    BrowserFormsCapability,
)
from agentx.capabilities.browser_session_actions import (
    BrowserDownloadObservation,
    BrowserSessionCapability,
    BrowserSessionOperation,
)
from agentx.capabilities.browser_provider import (
    BrowserProviderAvailability,
    BrowserProviderDescriptor,
    BrowserProviderId,
    BrowserProviderStatus,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "ChromeDriverService",
    "WebDriverBrowserProvider",
    "WebDriverHealth",
]

_PROVIDER_ID: Final[BrowserProviderId] = BrowserProviderId("webdriver.chrome")
_ELEMENT_KEY: Final[str] = "element-6066-11e4-a52e-4f735466cecf"
_MAX_BODY_BYTES: Final[int] = 16 * 1024 * 1024
_DEFAULT_TIMEOUT: Final[float] = 10.0
_OBSERVED_ATTRIBUTES: Final[tuple[str, ...]] = (
    "id",
    "name",
    "type",
    "value",
    "role",
    "aria-label",
    "href",
    "src",
    "placeholder",
)


def _error(code: str, message: str, *, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True, slots=True)
class WebDriverHealth:
    reachable: bool
    session_live: bool
    detail: str


class ChromeDriverService:
    """Trusted local service launcher with no caller-controlled executable."""

    __slots__ = ("_endpoint", "_process")

    def __init__(self, *, startup_timeout: float = 10.0) -> None:
        executable = shutil.which("chromedriver")
        if executable is None:
            raise RuntimeError("chromedriver is not available on the host PATH")
        if startup_timeout <= 0 or startup_timeout > 60:
            raise ValueError("startup_timeout must be within (0, 60]")
        port = _free_loopback_port()
        self._endpoint = f"http://127.0.0.1:{port}"
        self._process = subprocess.Popen(
            [executable, f"--port={port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("chromedriver terminated during startup")
            try:
                with urlopen(f"{self._endpoint}/status", timeout=0.25) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                time.sleep(0.05)
        self.close()
        raise TimeoutError("chromedriver did not become ready within the startup bound")

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def close(self) -> None:
        process = self._process
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def __enter__(self) -> ChromeDriverService:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class WebDriverBrowserProvider:
    """Concrete provider over one explicitly established W3C browser session."""

    __slots__ = (
        "_capabilities",
        "_connected",
        "_download_directory",
        "_endpoint",
        "_session_id",
        "_timeout",
    )

    def __init__(
        self,
        endpoint: str,
        *,
        headless: bool = True,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        download_directory: Path | None = None,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.startswith("http://127.0.0.1:"):
            raise ValueError("WebDriver endpoint must be an explicit loopback HTTP URL")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if download_directory is not None and (
            not isinstance(download_directory, Path) or not download_directory.is_absolute()
        ):
            raise ValueError("download_directory must be an absolute Path or None")
        self._endpoint = endpoint.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._download_directory = download_directory
        self._connected = False
        self._session_id = self._create_session(
            headless=headless,
            download_directory=download_directory,
        )
        self._connected = True
        self._capabilities = (
            BrowserActionsCapability(
                operation=BrowserActionOperation.NAVIGATE,
                provider=self,
                driver=self,
            ),
            BrowserActionsCapability(
                operation=BrowserActionOperation.CLICK_SELECTED,
                provider=self,
                driver=self,
            ),
            BrowserActionsCapability(
                operation=BrowserActionOperation.FILL_SELECTED,
                provider=self,
                driver=self,
            ),
            *(
                BrowserFormsCapability(operation=operation, provider=self, driver=self)
                for operation in BrowserFormOperation
            ),
            *(
                BrowserSessionCapability(operation=operation, provider=self, driver=self)
                for operation in BrowserSessionOperation
            ),
        )

    @property
    def descriptor(self) -> BrowserProviderDescriptor:
        return BrowserProviderDescriptor(
            provider_id=_PROVIDER_ID,
            description="Concrete Chrome provider over the W3C WebDriver protocol.",
        )

    @property
    def status(self) -> BrowserProviderStatus:
        return BrowserProviderStatus(
            availability=(
                BrowserProviderAvailability.AVAILABLE
                if self._connected
                else BrowserProviderAvailability.NOT_CONNECTED
            ),
            detail=(
                "an explicit W3C WebDriver session is established"
                if self._connected
                else "the W3C WebDriver session is closed"
            ),
        )

    @property
    def capabilities(self) -> tuple[Capability[Any], ...]:
        return self._capabilities

    @property
    def session_id(self) -> str:
        return self._session_id

    def close(self) -> None:
        if not self._connected:
            return
        try:
            self._request("DELETE", "")
        finally:
            self._connected = False

    def health(self) -> WebDriverHealth:
        try:
            raw = self._raw_request("GET", "/status")
            reachable = isinstance(raw, dict)
        except Exception:
            return WebDriverHealth(
                reachable=False,
                session_live=False,
                detail="WebDriver service is unreachable",
            )
        if not self._connected:
            return WebDriverHealth(
                reachable=reachable,
                session_live=False,
                detail="WebDriver service is reachable but the session is closed",
            )
        try:
            self._request("GET", "/window")
        except Exception:
            return WebDriverHealth(
                reachable=reachable,
                session_live=False,
                detail="WebDriver session liveness probe failed",
            )
        return WebDriverHealth(
            reachable=reachable,
            session_live=True,
            detail="WebDriver service and session are live",
        )

    def target_ref(self) -> Result[BrowserTargetRef, AgentXError]:
        try:
            handle = self._value(self._request("GET", "/window"))
            url = self._value(self._request("GET", "/url"))
            title = self._value(self._request("GET", "/title"))
            if not all(isinstance(item, str) for item in (handle, url, title)):
                raise ValueError("malformed WebDriver target metadata")
            return Result.success(self._target(str(handle), str(url), str(title)))
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.target_unavailable",
                    "WebDriver could not observe the active page target",
                    category=ErrorCategory.DEPENDENCY,
                )
            )

    def navigate(
        self, target: BrowserTargetRef, url: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        try:
            self._switch_target(target)
            self._request("POST", "/url", {"url": url})
            observed = self.target_ref().unwrap()
            return Result.success(
                BrowserActionOutcome(
                    succeeded=True,
                    message="WebDriver navigation completed",
                    observed_target=observed,
                )
            )
        except Exception:
            return self._action_failure("navigation")

    def click_selected(
        self, node: BrowserDomNodeRef
    ) -> Result[BrowserActionOutcome, AgentXError]:
        try:
            self._switch_target(node.target)
            self._request("POST", f"/element/{quote(node.node_id.value, safe='')}/click", {})
            return Result.success(
                BrowserActionOutcome(succeeded=True, message="WebDriver click completed")
            )
        except Exception:
            return self._action_failure("click")

    def fill_selected(
        self, node: BrowserDomNodeRef, text: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        try:
            self._switch_target(node.target)
            element = quote(node.node_id.value, safe="")
            self._request("POST", f"/element/{element}/clear", {})
            self._request(
                "POST",
                f"/element/{element}/value",
                {"text": text, "value": list(text)},
            )
            return Result.success(
                BrowserActionOutcome(succeeded=True, message="WebDriver fill completed")
            )
        except Exception:
            return self._action_failure("fill")

    def set_checked(
        self, node: BrowserDomNodeRef, checked: bool
    ) -> Result[BrowserActionOutcome, AgentXError]:
        try:
            self._switch_target(node.target)
            element = quote(node.node_id.value, safe="")
            selected = self._value(self._request("GET", f"/element/{element}/selected"))
            if not isinstance(selected, bool):
                raise ValueError("malformed selected state")
            if selected is not checked:
                self._request("POST", f"/element/{element}/click", {})
            return Result.success(
                BrowserActionOutcome(succeeded=True, message="WebDriver checked state completed")
            )
        except Exception:
            return self._action_failure("checked-state")

    def select_option(
        self, node: BrowserDomNodeRef, option_value: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        try:
            self._switch_target(node.target)
            element = quote(node.node_id.value, safe="")
            raw = self._value(
                self._request(
                    "POST",
                    f"/element/{element}/elements",
                    {"using": "tag name", "value": "option"},
                )
            )
            if not isinstance(raw, list):
                raise ValueError("malformed option list")
            matches: list[str] = []
            for item in raw:
                option_id = self._element_id(item)
                observed = self._value(
                    self._request(
                        "GET",
                        f"/element/{quote(option_id, safe='')}/attribute/value",
                    )
                )
                if observed == option_value:
                    matches.append(option_id)
            if len(matches) != 1:
                raise ValueError("select option must resolve to exactly one value match")
            self._request(
                "POST",
                f"/element/{quote(matches[0], safe='')}/click",
                {},
            )
            return Result.success(
                BrowserActionOutcome(succeeded=True, message="WebDriver option selection completed")
            )
        except Exception:
            return self._action_failure("option-selection")

    def submit_selected(
        self, node: BrowserDomNodeRef
    ) -> Result[BrowserActionOutcome, AgentXError]:
        return self.click_selected(node)

    def upload_selected(
        self, node: BrowserDomNodeRef, file_path: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        path = Path(file_path)
        if not path.is_absolute() or not path.is_file():
            return Result.failure(
                _error(
                    "browser.webdriver.upload_file_invalid",
                    "upload file must be an existing absolute regular file",
                    category=ErrorCategory.PRECONDITION,
                )
            )
        try:
            self._switch_target(node.target)
            element = quote(node.node_id.value, safe="")
            value = str(path)
            self._request(
                "POST",
                f"/element/{element}/value",
                {"text": value, "value": list(value)},
            )
            return Result.success(
                BrowserActionOutcome(succeeded=True, message="WebDriver file selection completed")
            )
        except Exception:
            return self._action_failure("file-selection")

    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]:
        try:
            self._switch_target(request.target)
            raw_elements = self._value(
                self._request(
                    "POST",
                    "/elements",
                    {"using": "xpath", "value": "//*"},
                )
            )
            if not isinstance(raw_elements, list):
                raise ValueError("malformed DOM element list")
            target_result = self.target_ref()
            if target_result.is_failure:
                return Result.failure(target_result.unwrap_error())
            target = target_result.unwrap()
            nodes: list[BrowserDomNodeSnapshot] = []
            root_id: BrowserDomNodeId | None = None
            for raw in raw_elements:
                element_id = self._element_id(raw)
                node_id = BrowserDomNodeId(target.target_id, element_id)
                node_ref = BrowserDomNodeRef(
                    target=target,
                    node_id=node_id,
                    state=BrowserDomNodeState.AVAILABLE,
                )
                encoded = quote(element_id, safe="")
                tag = self._string_value(
                    self._request("GET", f"/element/{encoded}/name")
                )
                text = self._string_value(
                    self._request("GET", f"/element/{encoded}/text")
                )
                displayed = self._bool_value(
                    self._request("GET", f"/element/{encoded}/displayed")
                )
                enabled = self._bool_value(
                    self._request("GET", f"/element/{encoded}/enabled")
                )
                selected = self._bool_value(
                    self._request("GET", f"/element/{encoded}/selected")
                )
                attributes: list[BrowserDomAttribute] = []
                for name in _OBSERVED_ATTRIBUTES:
                    value = self._value(
                        self._request(
                            "GET",
                            f"/element/{encoded}/attribute/{quote(name, safe='')}",
                        )
                    )
                    if isinstance(value, str):
                        attributes.append(BrowserDomAttribute(name=name, value=value))
                property_value = self._value(
                    self._request("GET", f"/element/{encoded}/property/value")
                )
                attributes = [item for item in attributes if item.name != "value"]
                if isinstance(property_value, str):
                    attributes.append(BrowserDomAttribute(name="value", value=property_value))
                if selected and tag in {"input", "option"}:
                    attributes.append(BrowserDomAttribute(name="checked", value="true"))
                role = next((item.value for item in attributes if item.name == "role"), None)
                accessible_name = next(
                    (item.value for item in attributes if item.name == "aria-label"),
                    None,
                )
                snapshot = BrowserDomNodeSnapshot(
                    node=node_ref,
                    tag_name=tag,
                    role=role,
                    name=accessible_name,
                    text=text,
                    attributes=tuple(attributes),
                    visible=displayed,
                    interactable=displayed and enabled,
                )
                nodes.append(snapshot)
                if root_id is None and tag == "html":
                    root_id = node_id
            if root_id is None and nodes:
                root_id = nodes[0].node.node_id
            page_source = self._string_value(self._request("GET", "/source"))
            document_version = hashlib.sha256(page_source.encode("utf-8")).hexdigest()
            return Result.success(
                BrowserDomObservation(
                    target=target,
                    state=BrowserDomObservationState.OBSERVED,
                    observed_at=datetime.now(UTC),
                    nodes=tuple(nodes),
                    root_id=root_id,
                    document_version=document_version,
                )
            )
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.dom_observation_failed",
                    "WebDriver structured DOM observation failed",
                    category=ErrorCategory.DEPENDENCY,
                )
            )

    def download_selected(
        self, node: BrowserDomNodeRef, expected_filename: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        if self._download_directory is None:
            return Result.failure(
                _error(
                    "browser.webdriver.download_directory_missing",
                    "WebDriver download directory is not configured",
                    category=ErrorCategory.PRECONDITION,
                )
            )
        destination = self._download_directory / expected_filename
        if destination.exists():
            destination.unlink()
        clicked = self.click_selected(node)
        if clicked.is_failure:
            return clicked
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if destination.is_file() and destination.stat().st_size > 0:
                return Result.success(
                    BrowserActionOutcome(
                        succeeded=True,
                        message="WebDriver download completed",
                    )
                )
            time.sleep(0.05)
        return Result.failure(
            _error(
                "browser.webdriver.download_timeout",
                "download artifact did not appear within the bounded timeout",
                category=ErrorCategory.TIMEOUT,
            )
        )

    def observe_download(
        self, expected_filename: str
    ) -> Result[BrowserDownloadObservation, AgentXError]:
        if self._download_directory is None:
            return Result.failure(
                _error(
                    "browser.webdriver.download_directory_missing",
                    "WebDriver download directory is not configured",
                    category=ErrorCategory.PRECONDITION,
                )
            )
        path = self._download_directory / expected_filename
        if not path.is_file():
            return Result.failure(
                _error(
                    "browser.webdriver.download_missing",
                    "download artifact was not found",
                    category=ErrorCategory.NOT_FOUND,
                )
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return Result.success(
            BrowserDownloadObservation(
                filename=expected_filename,
                size_bytes=path.stat().st_size,
                sha256=digest,
            )
        )

    def window_handles(self) -> Result[tuple[str, ...], AgentXError]:
        try:
            value = self._value(self._request("GET", "/window/handles"))
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("malformed window handles")
            return Result.success(tuple(value))
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.window_handles_failed",
                    "WebDriver window-handle observation failed",
                    category=ErrorCategory.DEPENDENCY,
                )
            )

    def switch_window(self, handle: str) -> Result[BrowserTargetRef, AgentXError]:
        if not isinstance(handle, str) or not handle:
            raise ValueError("handle must be a non-empty string")
        try:
            self._request("POST", "/window", {"handle": handle})
            return self.target_ref()
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.window_switch_failed",
                    "WebDriver could not switch to the requested window",
                    category=ErrorCategory.EXECUTION,
                )
            )

    def cookies(self) -> Result[tuple[dict[str, object], ...], AgentXError]:
        try:
            value = self._value(self._request("GET", "/cookie"))
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError("malformed cookies")
            return Result.success(tuple(dict(item) for item in value))
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.cookie_read_failed",
                    "WebDriver cookie observation failed",
                    category=ErrorCategory.DEPENDENCY,
                )
            )

    def set_cookie(self, cookie: dict[str, object]) -> Result[None, AgentXError]:
        try:
            self._request("POST", "/cookie", {"cookie": cookie})
            return Result.success(None)
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.cookie_write_failed",
                    "WebDriver cookie mutation failed",
                    category=ErrorCategory.EXECUTION,
                )
            )

    def delete_cookie(self, name: str) -> Result[None, AgentXError]:
        if not isinstance(name, str) or not name:
            raise ValueError("cookie name must be a non-empty string")
        try:
            self._request("DELETE", f"/cookie/{quote(name, safe='')}")
            return Result.success(None)
        except Exception:
            return Result.failure(
                _error(
                    "browser.webdriver.cookie_delete_failed",
                    "WebDriver cookie deletion failed",
                    category=ErrorCategory.EXECUTION,
                )
            )

    def _create_session(
        self, *, headless: bool, download_directory: Path | None
    ) -> str:
        arguments = ["--disable-gpu", "--window-size=1280,900"]
        if headless:
            arguments.append("--headless=new")
        chrome_options: dict[str, object] = {"args": arguments}
        if download_directory is not None:
            chrome_options["prefs"] = {
                "download.default_directory": str(download_directory),
                "download.prompt_for_download": False,
                "safebrowsing.enabled": True,
            }
        raw = self._raw_request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "chrome",
                        "goog:chromeOptions": chrome_options,
                    }
                }
            },
        )
        value = raw.get("value") if isinstance(raw, dict) else None
        if not isinstance(value, dict):
            raise RuntimeError("WebDriver session response is malformed")
        session_id = value.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            session_id = raw.get("sessionId") if isinstance(raw, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("WebDriver did not return a session id")
        return session_id

    def _request(
        self,
        method: str,
        suffix: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not self._session_id:
            raise RuntimeError("WebDriver session is not initialized")
        return self._raw_request(
            method,
            f"/session/{quote(self._session_id, safe='')}{suffix}",
            body,
        )

    def _raw_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self._endpoint}{path}",
            data=payload,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read(_MAX_BODY_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise RuntimeError("WebDriver transport request failed") from exc
        if len(raw) > _MAX_BODY_BYTES:
            raise RuntimeError("WebDriver response exceeded the bounded body limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("WebDriver response was malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("WebDriver response root must be an object")
        value = decoded.get("value")
        if isinstance(value, dict) and isinstance(value.get("error"), str):
            raise RuntimeError("WebDriver reported an operation failure")
        return decoded

    @staticmethod
    def _value(response: dict[str, object]) -> object:
        return response.get("value")

    @staticmethod
    def _element_id(raw: object) -> str:
        if not isinstance(raw, dict):
            raise ValueError("WebDriver element is malformed")
        value = raw.get(_ELEMENT_KEY)
        if not isinstance(value, str) or not value:
            raise ValueError("WebDriver element id is malformed")
        return value

    @staticmethod
    def _string_value(response: dict[str, object]) -> str:
        value = response.get("value")
        if not isinstance(value, str):
            raise ValueError("WebDriver string response is malformed")
        return value

    @staticmethod
    def _bool_value(response: dict[str, object]) -> bool:
        value = response.get("value")
        if not isinstance(value, bool):
            raise ValueError("WebDriver boolean response is malformed")
        return value

    def _target(self, handle: str, url: str, title: str) -> BrowserTargetRef:
        session = BrowserSessionId(_PROVIDER_ID, self._session_id)
        connection = BrowserConnectionRef(
            session_id=session,
            state=BrowserConnectionState.CONNECTED,
            detail="W3C WebDriver session",
        )
        return BrowserTargetRef(
            connection=connection,
            target_id=BrowserTargetId(session, handle),
            kind=BrowserTargetKind.PAGE,
            state=BrowserTargetState.AVAILABLE,
            title=title,
            url=url,
        )

    def _switch_target(self, target: BrowserTargetRef) -> None:
        if target.provider_id != _PROVIDER_ID:
            raise ValueError("target belongs to a different browser provider")
        if target.session_id.value != self._session_id:
            raise ValueError("target belongs to a different WebDriver session")
        self._request("POST", "/window", {"handle": target.target_id.value})

    @staticmethod
    def _action_failure(operation: str) -> Result[BrowserActionOutcome, AgentXError]:
        return Result.failure(
            _error(
                "browser.webdriver.action_failed",
                f"WebDriver {operation} failed",
                category=ErrorCategory.EXECUTION,
            )
        )
