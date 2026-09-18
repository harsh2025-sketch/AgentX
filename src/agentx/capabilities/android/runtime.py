"""Governed Android operations backed by the bounded M13 ADB transport."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

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
from agentx.capabilities.android.transport import (
    ADB_SCREENSHOT_OUTPUT_LIMIT,
    AdbCommandResult,
    AdbTransport,
    validate_adb_serial,
    validate_android_component,
    validate_android_package,
)
from agentx.capabilities.android.ui import (
    AndroidTargetSelector,
    AndroidUiTree,
    AndroidUiValidationError,
    parse_android_ui_tree,
    resolve_android_target,
)
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "ANDROID_CAPABILITY_IDENTITIES",
    "AndroidActionCapability",
    "AndroidActionParams",
    "AndroidDriver",
    "AndroidNavigation",
    "AndroidOperation",
    "AndroidScreenCapture",
    "AndroidVerificationSpec",
    "build_android_capabilities",
]

_MAX_TEXT_ENTRY: Final[int] = 1024
_UI_DUMP_PATH: Final[str] = "/sdcard/agentx-window.xml"
_PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"


class AndroidOperation(StrEnum):
    PACKAGE_DISCOVERY = "package_discovery"
    APP_LAUNCH = "app_launch"
    ACCESSIBILITY_TREE = "accessibility_tree"
    TAP = "tap"
    TEXT_ENTRY = "text_entry"
    SWIPE = "swipe"
    NAVIGATION = "navigation"
    SCREEN_CAPTURE = "screen_capture"


class AndroidNavigation(StrEnum):
    BACK = "back"
    HOME = "home"


def _identity(name: str) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName(name),
        version=CapabilityVersion(major=1, minor=0, patch=0),
    )


ANDROID_CAPABILITY_IDENTITIES: Final[dict[AndroidOperation, CapabilityIdentity]] = {
    AndroidOperation.PACKAGE_DISCOVERY: _identity("android.package_discovery"),
    AndroidOperation.APP_LAUNCH: _identity("android.app_launch"),
    AndroidOperation.ACCESSIBILITY_TREE: _identity("android.accessibility_tree"),
    AndroidOperation.TAP: _identity("android.tap"),
    AndroidOperation.TEXT_ENTRY: _identity("android.text_entry"),
    AndroidOperation.SWIPE: _identity("android.swipe"),
    AndroidOperation.NAVIGATION: _identity("android.navigation"),
    AndroidOperation.SCREEN_CAPTURE: _identity("android.screen_capture"),
}


def _selector_to_dict(selector: AndroidTargetSelector | None) -> JsonValue:
    if selector is None:
        return None
    return {
        "resource_id": selector.resource_id,
        "text": selector.text,
        "content_description": selector.content_description,
        "class_name": selector.class_name,
        "package": selector.package,
        "require_enabled": selector.require_enabled,
        "require_clickable": selector.require_clickable,
        "require_visible": selector.require_visible,
    }


@dataclass(frozen=True, slots=True)
class AndroidVerificationSpec:
    """Independent postcondition requirements for mutating Android actions."""

    expected_foreground_package: str | None = None
    target_present: AndroidTargetSelector | None = None
    target_absent: AndroidTargetSelector | None = None

    def __post_init__(self) -> None:
        if self.expected_foreground_package is not None:
            validate_android_package(self.expected_foreground_package)
        if self.target_present is not None and not isinstance(
            self.target_present, AndroidTargetSelector
        ):
            raise TypeError("target_present must be AndroidTargetSelector or None")
        if self.target_absent is not None and not isinstance(
            self.target_absent, AndroidTargetSelector
        ):
            raise TypeError("target_absent must be AndroidTargetSelector or None")
        if (
            self.expected_foreground_package is None
            and self.target_present is None
            and self.target_absent is None
        ):
            raise ValueError("verification spec requires at least one independent postcondition")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "expected_foreground_package": self.expected_foreground_package,
            "target_present": _selector_to_dict(self.target_present),
            "target_absent": _selector_to_dict(self.target_absent),
        }


@dataclass(frozen=True, slots=True)
class AndroidActionParams(CapabilityParams):
    """One typed Android operation. Unused fields are rejected."""

    operation: AndroidOperation
    serial: str
    package: str | None = None
    component: str | None = None
    selector: AndroidTargetSelector | None = None
    text: str | None = None
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    navigation: AndroidNavigation | None = None
    verification: AndroidVerificationSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AndroidOperation):
            raise TypeError("operation must be AndroidOperation")
        validate_adb_serial(self.serial)
        if self.package is not None:
            validate_android_package(self.package)
        if self.component is not None:
            validate_android_component(self.component)
        if self.selector is not None and not isinstance(self.selector, AndroidTargetSelector):
            raise TypeError("selector must be AndroidTargetSelector or None")
        if self.verification is not None and not isinstance(
            self.verification, AndroidVerificationSpec
        ):
            raise TypeError("verification must be AndroidVerificationSpec or None")
        self._validate_shape()

    def _validate_shape(self) -> None:
        coordinate_values = (self.x, self.y, self.x2, self.y2, self.duration_ms)
        for name, value in zip(
            ("x", "y", "x2", "y2", "duration_ms"), coordinate_values, strict=True
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative int or None")

        if self.operation is AndroidOperation.PACKAGE_DISCOVERY:
            self._reject_extras()
            return
        if self.operation is AndroidOperation.APP_LAUNCH:
            if self.package is None:
                raise ValueError("app_launch requires package")
            if self.component is not None:
                component_package = self.component.partition("/")[0]
                if component_package != self.package:
                    raise ValueError("component package must match requested package")
            self._reject_extras(allow={"package", "component", "verification"})
            return
        if self.operation is AndroidOperation.ACCESSIBILITY_TREE:
            self._reject_extras()
            return
        if self.operation is AndroidOperation.TAP:
            semantic = self.selector is not None
            coordinates = self.x is not None or self.y is not None
            if semantic == coordinates:
                raise ValueError("tap requires exactly one of semantic selector or x/y coordinates")
            if coordinates and (self.x is None or self.y is None):
                raise ValueError("tap coordinates require both x and y")
            self._reject_extras(allow={"selector", "x", "y", "verification"})
            return
        if self.operation is AndroidOperation.TEXT_ENTRY:
            if not isinstance(self.text, str):
                raise TypeError("text_entry requires text")
            if len(self.text) > _MAX_TEXT_ENTRY:
                raise ValueError("text entry exceeds bounded length")
            if any(character in self.text for character in ("\x00", "\r", "\n")):
                raise ValueError("text entry must not contain NUL/CR/LF")
            self._reject_extras(allow={"text", "verification"})
            return
        if self.operation is AndroidOperation.SWIPE:
            if None in (self.x, self.y, self.x2, self.y2, self.duration_ms):
                raise ValueError("swipe requires start/end coordinates and duration_ms")
            assert self.duration_ms is not None
            if not 1 <= self.duration_ms <= 60_000:
                raise ValueError("duration_ms must be in [1, 60000]")
            self._reject_extras(
                allow={"x", "y", "x2", "y2", "duration_ms", "verification"}
            )
            return
        if self.operation is AndroidOperation.NAVIGATION:
            if not isinstance(self.navigation, AndroidNavigation):
                raise TypeError("navigation operation requires AndroidNavigation")
            self._reject_extras(allow={"navigation", "verification"})
            return
        if self.operation is AndroidOperation.SCREEN_CAPTURE:
            self._reject_extras()
            return
        raise ValueError("unsupported Android operation")

    def _reject_extras(self, *, allow: set[str] | None = None) -> None:
        allowed = set() if allow is None else allow
        values: dict[str, object | None] = {
            "package": self.package,
            "component": self.component,
            "selector": self.selector,
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "duration_ms": self.duration_ms,
            "navigation": self.navigation,
            "verification": self.verification,
        }
        unexpected = [name for name, value in values.items() if value is not None and name not in allowed]
        if unexpected:
            raise ValueError(f"{self.operation.value} rejects fields: {sorted(unexpected)}")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": self.operation.value,
            "serial": self.serial,
            "package": self.package,
            "component": self.component,
            "selector": _selector_to_dict(self.selector),
            "text": None if self.text is None else "[REDACTED]",
            "text_length": None if self.text is None else len(self.text),
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "duration_ms": self.duration_ms,
            "navigation": None if self.navigation is None else self.navigation.value,
            "verification": (
                None if self.verification is None else self.verification.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class AndroidScreenCapture:
    serial: str
    png: bytes
    width: int
    height: int
    sha256: str

    def __post_init__(self) -> None:
        validate_adb_serial(self.serial)
        if not isinstance(self.png, bytes) or not self.png.startswith(_PNG_SIGNATURE):
            raise ValueError("png must be valid PNG bytes")
        if type(self.width) is not int or self.width < 1:
            raise ValueError("width must be positive int")
        if type(self.height) is not int or self.height < 1:
            raise ValueError("height must be positive int")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("sha256 must be lowercase hex digest")


class AndroidDriver:
    """Fixed Android operations. It exposes no arbitrary host command capability."""

    def __init__(self, transport: AdbTransport) -> None:
        if not isinstance(transport, AdbTransport):
            raise TypeError("transport must be AdbTransport")
        self._transport = transport

    @property
    def transport(self) -> AdbTransport:
        return self._transport

    def packages(self, serial: str, *, context: ExecutionContext) -> tuple[str, ...]:
        output = self._shell_text(
            serial,
            ("pm", "list", "packages"),
            context=context,
            code="android.packages.failed",
        )
        packages: set[str] = set()
        for raw in output.splitlines():
            line = raw.strip()
            if not line:
                continue
            prefix = "package:"
            if not line.startswith(prefix):
                raise RuntimeError("android.packages.malformed")
            package = validate_android_package(line[len(prefix) :])
            packages.add(package)
        return tuple(sorted(packages))

    def resolve_launcher(
        self,
        serial: str,
        package: str,
        *,
        context: ExecutionContext,
    ) -> str:
        checked_package = validate_android_package(package)
        output = self._shell_text(
            serial,
            ("cmd", "package", "resolve-activity", "--brief", checked_package),
            context=context,
            code="android.launch.resolve_failed",
        )
        lines = tuple(line.strip() for line in output.splitlines() if line.strip())
        for candidate in reversed(lines):
            try:
                component = validate_android_component(candidate)
            except ValueError:
                continue
            if component.partition("/")[0] == checked_package:
                return component
        raise RuntimeError("android.launch.activity_missing")

    def launch(
        self,
        serial: str,
        package: str,
        component: str | None,
        *,
        context: ExecutionContext,
    ) -> str:
        checked_package = validate_android_package(package)
        actual_component = (
            self.resolve_launcher(serial, checked_package, context=context)
            if component is None
            else validate_android_component(component)
        )
        if actual_component.partition("/")[0] != checked_package:
            raise ValueError("launch component package mismatch")
        result = self._transport.shell(
            serial,
            ("am", "start", "-W", "-n", actual_component),
            context=context,
        )
        self._require_success(result, "android.launch.failed")
        return actual_component

    def foreground_package(self, serial: str, *, context: ExecutionContext) -> str | None:
        output = self._shell_text(
            serial,
            ("dumpsys", "activity", "activities"),
            context=context,
            code="android.foreground.failed",
        )
        patterns = (
            re.compile(r"mResumedActivity:.*? ([A-Za-z][A-Za-z0-9_.]+)/"),
            re.compile(r"topResumedActivity=.*? ([A-Za-z][A-Za-z0-9_.]+)/"),
        )
        for line in output.splitlines():
            for pattern in patterns:
                match = pattern.search(line)
                if match is not None:
                    try:
                        return validate_android_package(match.group(1))
                    except ValueError:
                        return None
        return None

    def ui_tree(self, serial: str, *, context: ExecutionContext) -> AndroidUiTree:
        dump = self._transport.shell(
            serial,
            ("uiautomator", "dump", _UI_DUMP_PATH),
            context=context,
        )
        self._require_success(dump, "android.accessibility.dump_failed")
        result = self._transport.exec_out(
            serial,
            ("cat", _UI_DUMP_PATH),
            context=context,
            max_output_bytes=2 * 1_048_576,
        )
        command = self._require_success(result, "android.accessibility.read_failed")
        return parse_android_ui_tree(command.stdout)

    def tap(
        self,
        serial: str,
        *,
        x: int,
        y: int,
        context: ExecutionContext,
    ) -> None:
        _validate_coordinate(x, "x")
        _validate_coordinate(y, "y")
        result = self._transport.shell(
            serial,
            ("input", "tap", str(x), str(y)),
            context=context,
        )
        self._require_success(result, "android.tap.failed")

    def enter_text(self, serial: str, text: str, *, context: ExecutionContext) -> None:
        if not isinstance(text, str):
            raise TypeError("text must be str")
        if len(text) > _MAX_TEXT_ENTRY or any(
            character in text for character in ("\x00", "\r", "\n")
        ):
            raise ValueError("text violates Android text-entry safety bounds")
        result = self._transport.shell(
            serial,
            ("input", "text", text),
            context=context,
        )
        self._require_success(result, "android.text.failed")

    def swipe(
        self,
        serial: str,
        *,
        x: int,
        y: int,
        x2: int,
        y2: int,
        duration_ms: int,
        context: ExecutionContext,
    ) -> None:
        for name, value in (("x", x), ("y", y), ("x2", x2), ("y2", y2)):
            _validate_coordinate(value, name)
        if type(duration_ms) is not int or not 1 <= duration_ms <= 60_000:
            raise ValueError("duration_ms must be in [1, 60000]")
        result = self._transport.shell(
            serial,
            ("input", "swipe", str(x), str(y), str(x2), str(y2), str(duration_ms)),
            context=context,
        )
        self._require_success(result, "android.swipe.failed")

    def navigate(
        self,
        serial: str,
        navigation: AndroidNavigation,
        *,
        context: ExecutionContext,
    ) -> None:
        if not isinstance(navigation, AndroidNavigation):
            raise TypeError("navigation must be AndroidNavigation")
        keycode = "4" if navigation is AndroidNavigation.BACK else "3"
        result = self._transport.shell(
            serial,
            ("input", "keyevent", keycode),
            context=context,
        )
        self._require_success(result, "android.navigation.failed")

    def screen_capture(
        self,
        serial: str,
        *,
        context: ExecutionContext,
    ) -> AndroidScreenCapture:
        result = self._transport.exec_out(
            serial,
            ("screencap", "-p"),
            context=context,
            max_output_bytes=ADB_SCREENSHOT_OUTPUT_LIMIT,
        )
        command = self._require_success(result, "android.screen.failed")
        payload = command.stdout
        if len(payload) < 24 or not payload.startswith(_PNG_SIGNATURE):
            raise RuntimeError("android.screen.malformed_png")
        width, height = struct.unpack(">II", payload[16:24])
        if width < 1 or height < 1 or width > 16_384 or height > 16_384:
            raise RuntimeError("android.screen.invalid_dimensions")
        return AndroidScreenCapture(
            serial=serial,
            png=payload,
            width=width,
            height=height,
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    def _shell_text(
        self,
        serial: str,
        tokens: tuple[str, ...],
        *,
        context: ExecutionContext,
        code: str,
    ) -> str:
        result = self._transport.shell(serial, tokens, context=context)
        command = self._require_success(result, code)
        try:
            return command.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"{code}.non_utf8") from exc

    @staticmethod
    def _require_success(
        result: object,
        code: str,
    ) -> AdbCommandResult:
        from agentx.core.result import Result

        if not isinstance(result, Result):
            raise TypeError("transport result must be Result")
        if result.is_failure:
            raise RuntimeError(result.unwrap_error().code)
        command = result.unwrap()
        if not isinstance(command, AdbCommandResult):
            raise TypeError("transport success must contain AdbCommandResult")
        if not command.succeeded:
            raise RuntimeError(code)
        return command


class AndroidActionCapability:
    """One fixed Android operation exposed through the canonical governed ABI."""

    def __init__(self, *, operation: AndroidOperation, driver: AndroidDriver) -> None:
        if not isinstance(operation, AndroidOperation):
            raise TypeError("operation must be AndroidOperation")
        if not isinstance(driver, AndroidDriver):
            raise TypeError("driver must be AndroidDriver")
        self._operation = operation
        self._driver = driver
        self._descriptor = _descriptor(operation)

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[AndroidActionParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        params = self._require_request(request)
        try:
            data = self._execute(params, context)
        except (RuntimeError, AndroidUiValidationError, ValueError) as exc:
            return ExecutionResult(
                succeeded=False,
                message=f"Android {self._operation.value} failed",
                observation=CapabilityObservation(
                    summary="Android operation failure evidence",
                    data={"operation": self._operation.value, "error": str(exc)[:512]},
                ),
            )
        return ExecutionResult(
            succeeded=True,
            message=f"Android {self._operation.value} executed",
            observation=CapabilityObservation(
                summary="Android operation execution evidence",
                data=data,
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[AndroidActionParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        params = self._require_request(request)
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        try:
            return self._verify(params, observation, context)
        except (RuntimeError, AndroidUiValidationError, ValueError) as exc:
            return VerificationResult(
                passed=False,
                detail=f"independent Android verification failed: {str(exc)[:512]}",
            )

    def _require_request(
        self,
        request: CapabilityRequest[AndroidActionParams],
    ) -> AndroidActionParams:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be CapabilityRequest")
        if request.identity != self._descriptor.identity:
            raise ValueError("request identity does not match Android capability")
        if not isinstance(request.params, AndroidActionParams):
            raise TypeError("Android capability requires AndroidActionParams")
        if request.params.operation is not self._operation:
            raise ValueError("Android operation does not match capability identity")
        return request.params

    def _execute(
        self,
        params: AndroidActionParams,
        context: ExecutionContext,
    ) -> dict[str, JsonValue]:
        operation = params.operation
        if operation is AndroidOperation.PACKAGE_DISCOVERY:
            packages = self._driver.packages(params.serial, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "packages": list(packages),
            }
        if operation is AndroidOperation.APP_LAUNCH:
            assert params.package is not None
            component = self._driver.launch(
                params.serial,
                params.package,
                params.component,
                context=context,
            )
            return {
                "operation": operation.value,
                "serial": params.serial,
                "package": params.package,
                "component": component,
            }
        if operation is AndroidOperation.ACCESSIBILITY_TREE:
            tree = self._driver.ui_tree(params.serial, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "node_count": len(tree.nodes),
                "tree_digest": _tree_digest(tree),
            }
        if operation is AndroidOperation.TAP:
            if params.selector is not None:
                tree = self._driver.ui_tree(params.serial, context=context)
                target = resolve_android_target(tree, params.selector)
                x, y = target.bounds.center
            else:
                assert params.x is not None and params.y is not None
                x, y = params.x, params.y
            self._driver.tap(params.serial, x=x, y=y, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "x": x,
                "y": y,
                "semantic": params.selector is not None,
            }
        if operation is AndroidOperation.TEXT_ENTRY:
            assert params.text is not None
            self._driver.enter_text(params.serial, params.text, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "text_length": len(params.text),
                "text": "[REDACTED]",
            }
        if operation is AndroidOperation.SWIPE:
            assert params.x is not None
            assert params.y is not None
            assert params.x2 is not None
            assert params.y2 is not None
            assert params.duration_ms is not None
            self._driver.swipe(
                params.serial,
                x=params.x,
                y=params.y,
                x2=params.x2,
                y2=params.y2,
                duration_ms=params.duration_ms,
                context=context,
            )
            return {
                "operation": operation.value,
                "serial": params.serial,
                "x": params.x,
                "y": params.y,
                "x2": params.x2,
                "y2": params.y2,
                "duration_ms": params.duration_ms,
            }
        if operation is AndroidOperation.NAVIGATION:
            assert params.navigation is not None
            self._driver.navigate(params.serial, params.navigation, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "navigation": params.navigation.value,
            }
        if operation is AndroidOperation.SCREEN_CAPTURE:
            capture = self._driver.screen_capture(params.serial, context=context)
            return {
                "operation": operation.value,
                "serial": params.serial,
                "width": capture.width,
                "height": capture.height,
                "sha256": capture.sha256,
                "bytes": len(capture.png),
            }
        raise AssertionError("unreachable Android operation")

    def _verify(
        self,
        params: AndroidActionParams,
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if params.operation is AndroidOperation.PACKAGE_DISCOVERY:
            previous = observation.data.get("packages")
            current = list(self._driver.packages(params.serial, context=context))
            passed = previous == tuple(current) or previous == current
            return VerificationResult(
                passed=passed,
                detail=(
                    "package discovery independently reproduced the package set"
                    if passed
                    else "package set changed between execution and verification"
                ),
            )
        if params.operation is AndroidOperation.ACCESSIBILITY_TREE:
            tree = self._driver.ui_tree(params.serial, context=context)
            return VerificationResult(
                passed=True,
                detail=f"fresh accessibility hierarchy parsed with {len(tree.nodes)} nodes",
            )
        if params.operation is AndroidOperation.SCREEN_CAPTURE:
            capture = self._driver.screen_capture(params.serial, context=context)
            return VerificationResult(
                passed=capture.width > 0 and capture.height > 0,
                detail="fresh screenshot capture produced independently validated PNG dimensions",
            )
        if params.operation is AndroidOperation.APP_LAUNCH and params.verification is None:
            assert params.package is not None
            actual = self._driver.foreground_package(params.serial, context=context)
            return VerificationResult(
                passed=actual == params.package,
                detail="foreground package independently matches requested application"
                if actual == params.package
                else "requested application is not independently observed in foreground",
            )
        if params.verification is None:
            return VerificationResult(
                passed=False,
                detail="mutating Android operation has no independent postcondition",
            )
        return self._verify_spec(params.serial, params.verification, context)

    def _verify_spec(
        self,
        serial: str,
        spec: AndroidVerificationSpec,
        context: ExecutionContext,
    ) -> VerificationResult:
        if spec.expected_foreground_package is not None:
            actual = self._driver.foreground_package(serial, context=context)
            if actual != spec.expected_foreground_package:
                return VerificationResult(
                    passed=False,
                    detail="foreground package does not satisfy Android postcondition",
                )
        tree: AndroidUiTree | None = None
        if spec.target_present is not None or spec.target_absent is not None:
            tree = self._driver.ui_tree(serial, context=context)
        if spec.target_present is not None:
            assert tree is not None
            try:
                resolve_android_target(tree, spec.target_present)
            except AndroidUiValidationError:
                return VerificationResult(
                    passed=False,
                    detail="required semantic target is not uniquely present",
                )
        if spec.target_absent is not None:
            assert tree is not None
            try:
                resolve_android_target(tree, spec.target_absent)
            except AndroidUiValidationError as exc:
                if "not found" not in str(exc):
                    return VerificationResult(
                        passed=False,
                        detail="semantic absence postcondition is ambiguous",
                    )
            else:
                return VerificationResult(
                    passed=False,
                    detail="target expected absent is still present",
                )
        return VerificationResult(
            passed=True,
            detail="fresh Android state satisfies every independent postcondition",
        )


def _tree_digest(tree: AndroidUiTree) -> str:
    serializable = [
        {
            "resource_id": node.resource_id,
            "text": node.text,
            "content_description": node.content_description,
            "class_name": node.class_name,
            "package": node.package,
            "bounds": [
                node.bounds.left,
                node.bounds.top,
                node.bounds.right,
                node.bounds.bottom,
            ],
            "enabled": node.enabled,
            "clickable": node.clickable,
            "focusable": node.focusable,
            "visible": node.visible,
        }
        for node in tree.nodes
    ]
    encoded = json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_coordinate(value: object, field_name: str) -> int:
    if type(value) is not int or not 0 <= value <= 100_000:
        raise ValueError(f"{field_name} must be an int in [0, 100000]")
    return value


def _descriptor(operation: AndroidOperation) -> CapabilityDescriptor:
    read_only = operation in {
        AndroidOperation.PACKAGE_DISCOVERY,
        AndroidOperation.ACCESSIBILITY_TREE,
        AndroidOperation.SCREEN_CAPTURE,
    }
    if read_only:
        permissions = frozenset({Permission.READ})
        risk = assess_risk(
            read_only=True,
            modifies_state=False,
            reversible=False,
            external_effect=False,
        )
        rollback = RollbackDeclaration(
            support=RollbackSupport.NOT_APPLICABLE,
            detail="read-only Android observation has no state change to roll back",
        )
    elif operation is AndroidOperation.TEXT_ENTRY:
        permissions = frozenset({Permission.WRITE, Permission.EXECUTE})
        risk = assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=False,
        )
        rollback = RollbackDeclaration(
            support=RollbackSupport.UNSUPPORTED,
            detail="text entry may modify application state and has no generic rollback",
        )
    else:
        permissions = frozenset({Permission.EXECUTE})
        risk = assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=False,
        )
        rollback = RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="navigation-style Android mutation can normally be counteracted by navigation",
        )
    return CapabilityDescriptor(
        identity=ANDROID_CAPABILITY_IDENTITIES[operation],
        description=f"Governed Android {operation.value.replace('_', ' ')} operation",
        scope=CapabilityScope(platform=CapabilityPlatform.ANDROID),
        required_permissions=permissions,
        risk_assessment=risk,
        preconditions=(),
        rollback=rollback,
        estimate=ResourceEstimate(
            wall_clock=timedelta(seconds=10),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def build_android_capabilities(
    transport: AdbTransport,
) -> tuple[AndroidActionCapability, ...]:
    """Build every fixed Android capability over one bounded transport."""
    driver = AndroidDriver(transport)
    return tuple(
        AndroidActionCapability(operation=operation, driver=driver)
        for operation in AndroidOperation
    )
