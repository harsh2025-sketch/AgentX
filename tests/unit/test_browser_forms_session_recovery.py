"""Acceptance-focused tests for the M7 form/session/recovery additions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentx.capabilities.abi import Capability, CapabilityObservation
from agentx.capabilities.browser_actions import BrowserActionOutcome, BrowserClickPostcondition
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
    select_option_request,
    set_checked_request,
    submit_selected_request,
    upload_selected_request,
)
from agentx.capabilities.browser_provider import (
    BrowserProviderAvailability,
    BrowserProviderDescriptor,
    BrowserProviderId,
    BrowserProviderStatus,
)
from agentx.capabilities.browser_recovery import BrowserSelectionRecovery
from agentx.capabilities.browser_selection import BrowserDomSelector
from agentx.capabilities.browser_session_actions import (
    BrowserCookie,
    BrowserDownloadObservation,
    BrowserSessionCapability,
    BrowserSessionOperation,
    delete_cookie_request,
    download_selected_request,
    set_cookie_request,
    switch_window_request,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result

_T0 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
_PROVIDER = BrowserProviderId("browser.test")
_SESSION = BrowserSessionId(_PROVIDER, "session-1")
_CONNECTION = BrowserConnectionRef(
    session_id=_SESSION,
    state=BrowserConnectionState.CONNECTED,
    detail="test connection",
)


def _target(
    url: str = "https://example.invalid/form",
    handle: str = "window-1",
) -> BrowserTargetRef:
    return BrowserTargetRef(
        connection=_CONNECTION,
        target_id=BrowserTargetId(_SESSION, handle),
        kind=BrowserTargetKind.PAGE,
        state=BrowserTargetState.AVAILABLE,
        title="Fixture",
        url=url,
    )


def _node(target: BrowserTargetRef, value: str) -> BrowserDomNodeRef:
    return BrowserDomNodeRef(
        target=target,
        node_id=BrowserDomNodeId(target.target_id, value),
        state=BrowserDomNodeState.AVAILABLE,
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
    )


class _Surface:
    def __init__(self, tmp_path: Path) -> None:
        self.target = _target()
        self.values: dict[str, str] = {}
        self.checked: set[str] = set()
        self.cookies_state: dict[str, str] = {}
        self.download = tmp_path / "fixture.txt"
        self.download.write_bytes(b"agentx-browser-download")
        self.active = "window-1"
        self.calls: list[str] = []

    @property
    def descriptor(self) -> BrowserProviderDescriptor:
        return BrowserProviderDescriptor(_PROVIDER, "test browser surface")

    @property
    def status(self) -> BrowserProviderStatus:
        return BrowserProviderStatus(BrowserProviderAvailability.AVAILABLE, "available")

    @property
    def capabilities(self) -> tuple[Capability[Any], ...]:
        return ()

    def set_checked(
        self, node: BrowserDomNodeRef, checked: bool
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("set_checked")
        if checked:
            self.checked.add(node.node_id.value)
        else:
            self.checked.discard(node.node_id.value)
        return Result.success(BrowserActionOutcome(True, "checked"))

    def select_option(
        self, node: BrowserDomNodeRef, option_value: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("select_option")
        self.values[node.node_id.value] = option_value
        return Result.success(BrowserActionOutcome(True, "selected"))

    def submit_selected(self, node: BrowserDomNodeRef) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("submit_selected")
        self.target = _target("https://example.invalid/success")
        return Result.success(BrowserActionOutcome(True, "submitted"))

    def upload_selected(
        self, node: BrowserDomNodeRef, file_path: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("upload_selected")
        self.values[node.node_id.value] = Path(file_path).name
        return Result.success(BrowserActionOutcome(True, "uploaded"))

    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]:
        nodes: list[BrowserDomNodeSnapshot] = []
        for node_id in sorted(set(self.values) | self.checked):
            attributes: list[BrowserDomAttribute] = []
            if node_id in self.values:
                attributes.append(BrowserDomAttribute("value", self.values[node_id]))
            if node_id in self.checked:
                attributes.append(BrowserDomAttribute("checked", "true"))
            ref = _node(self.target, node_id)
            nodes.append(
                BrowserDomNodeSnapshot(
                    node=ref,
                    tag_name="input",
                    attributes=tuple(attributes),
                    visible=True,
                    interactable=True,
                )
            )
        return Result.success(
            BrowserDomObservation(
                target=self.target,
                state=BrowserDomObservationState.OBSERVED,
                observed_at=_T0,
                nodes=tuple(nodes),
                root_id=None,
                document_version="fresh",
            )
        )

    def download_selected(
        self, node: BrowserDomNodeRef, expected_filename: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("download_selected")
        return Result.success(BrowserActionOutcome(True, "downloaded"))

    def observe_download(
        self, expected_filename: str
    ) -> Result[BrowserDownloadObservation, AgentXError]:
        return Result.success(
            BrowserDownloadObservation(
                filename=expected_filename,
                size_bytes=self.download.stat().st_size,
                sha256=hashlib.sha256(self.download.read_bytes()).hexdigest(),
            )
        )

    def cookies(self) -> Result[tuple[dict[str, object], ...], AgentXError]:
        return Result.success(
            tuple({"name": name, "value": value} for name, value in self.cookies_state.items())
        )

    def set_cookie(self, cookie: dict[str, object]) -> Result[None, AgentXError]:
        self.calls.append("set_cookie")
        self.cookies_state[str(cookie["name"])] = str(cookie["value"])
        return Result.success(None)

    def delete_cookie(self, name: str) -> Result[None, AgentXError]:
        self.calls.append("delete_cookie")
        self.cookies_state.pop(name, None)
        return Result.success(None)

    def switch_window(self, handle: str) -> Result[BrowserTargetRef, AgentXError]:
        self.calls.append("switch_window")
        self.active = handle
        self.target = _target(handle=handle)
        return Result.success(self.target)

    def target_ref(self) -> Result[BrowserTargetRef, AgentXError]:
        return Result.success(self.target)


def _verify(
    capability: BrowserFormsCapability | BrowserSessionCapability,
    request: Any,
    observation: CapabilityObservation,
) -> bool:
    return capability.verify(request, observation, _context()).passed


def test_fill_related_form_operations_are_explicit_and_independently_verified(
    tmp_path: Path,
) -> None:
    surface = _Surface(tmp_path)
    target = surface.target
    checkbox = _node(target, "checkbox")
    select = _node(target, "select")
    upload = _node(target, "upload")
    submit = _node(target, "submit")

    check_cap = BrowserFormsCapability(
        operation=BrowserFormOperation.SET_CHECKED,
        provider=surface,
        driver=surface,
    )
    check_request = set_checked_request(target, checkbox, True)
    check_result = check_cap.execute(check_request, _context())
    assert check_result.succeeded
    assert _verify(check_cap, check_request, check_result.observation)

    select_cap = BrowserFormsCapability(
        operation=BrowserFormOperation.SELECT_OPTION,
        provider=surface,
        driver=surface,
    )
    select_request = select_option_request(target, select, "private-option")
    assert "private-option" not in str(select_request.params.to_dict())
    select_result = select_cap.execute(select_request, _context())
    assert _verify(select_cap, select_request, select_result.observation)

    upload_file = tmp_path / "sensitive-upload.txt"
    upload_file.write_text("secret", encoding="utf-8")
    upload_cap = BrowserFormsCapability(
        operation=BrowserFormOperation.UPLOAD_SELECTED,
        provider=surface,
        driver=surface,
    )
    upload_request = upload_selected_request(target, upload, str(upload_file))
    assert str(upload_file) not in str(upload_request.params.to_dict())
    upload_result = upload_cap.execute(upload_request, _context())
    assert _verify(upload_cap, upload_request, upload_result.observation)

    # Nothing above implicitly submitted the form.
    assert "submit_selected" not in surface.calls
    submit_cap = BrowserFormsCapability(
        operation=BrowserFormOperation.SUBMIT_SELECTED,
        provider=surface,
        driver=surface,
    )
    submit_request = submit_selected_request(
        target,
        submit,
        postcondition=BrowserClickPostcondition("https://example.invalid/success"),
    )
    submit_result = submit_cap.execute(submit_request, _context())
    assert _verify(submit_cap, submit_request, submit_result.observation)


def test_cookie_download_and_window_actions_verify_without_leaking_secrets(
    tmp_path: Path,
) -> None:
    surface = _Surface(tmp_path)
    target = surface.target

    cookie_cap = BrowserSessionCapability(
        operation=BrowserSessionOperation.SET_COOKIE,
        provider=surface,
        driver=surface,
    )
    cookie = BrowserCookie(name="session", value="super-secret-cookie")
    cookie_request = set_cookie_request(target, cookie)
    assert "super-secret-cookie" not in str(cookie_request.params.to_dict())
    cookie_result = cookie_cap.execute(cookie_request, _context())
    assert _verify(cookie_cap, cookie_request, cookie_result.observation)

    delete_cap = BrowserSessionCapability(
        operation=BrowserSessionOperation.DELETE_COOKIE,
        provider=surface,
        driver=surface,
    )
    delete_request = delete_cookie_request(target, "session")
    delete_result = delete_cap.execute(delete_request, _context())
    assert _verify(delete_cap, delete_request, delete_result.observation)

    download_cap = BrowserSessionCapability(
        operation=BrowserSessionOperation.DOWNLOAD_SELECTED,
        provider=surface,
        driver=surface,
    )
    download_request = download_selected_request(target, _node(target, "download"), "fixture.txt")
    download_result = download_cap.execute(download_request, _context())
    assert _verify(download_cap, download_request, download_result.observation)

    window_cap = BrowserSessionCapability(
        operation=BrowserSessionOperation.SWITCH_WINDOW,
        provider=surface,
        driver=surface,
    )
    window_request = switch_window_request(target, "window-2")
    window_result = window_cap.execute(window_request, _context())
    assert _verify(window_cap, window_request, window_result.observation)


def test_recovery_requires_one_unique_fresh_match(tmp_path: Path) -> None:
    surface = _Surface(tmp_path)
    target = surface.target
    surface.values["unique"] = "v"
    recovery = BrowserSelectionRecovery(surface)

    recovered = recovery.recover(
        BrowserDomReadRequest(target=target),
        BrowserDomSelector(attribute=BrowserDomAttribute("value", "v")),
    )
    assert recovered.unwrap().node.node_id.value == "unique"

    surface.values["second"] = "v"
    ambiguous = recovery.recover(
        BrowserDomReadRequest(target=target),
        BrowserDomSelector(attribute=BrowserDomAttribute("value", "v")),
    )
    assert ambiguous.unwrap_error().code == "browser.recovery.target_not_unique"
