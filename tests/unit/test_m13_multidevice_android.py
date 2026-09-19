# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityPlatform, CapabilityRequest
from agentx.capabilities.android import (
    AdbCommandResult,
    AdbTransport,
    AndroidActionParams,
    AndroidNavigation,
    AndroidOperation,
    AndroidProvider,
    AndroidTargetSelector,
    AndroidUiValidationError,
    AndroidVerificationSpec,
    build_android_capabilities,
    parse_android_ui_tree,
    resolve_android_target,
)
from agentx.capabilities.device import DevicePlatform
from agentx.capabilities.device_registry import (
    DeviceHealth,
    DeviceRegistry,
    evaluate_device_health,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.device_orchestration import DeviceRequirement, DeviceRouter


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeAdbRunner:
    def __init__(self, *, devices: str = "emulator-5554 device model:Pixel\n") -> None:
        self.devices_output = devices
        self.calls: list[tuple[str, ...]] = []
        self.foreground = "com.example.app"
        self.ui_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<hierarchy><node resource-id="com.example:id/go" text="Go" '
            b'content-desc="go" class="android.widget.Button" package="com.example.app" '
            b'enabled="true" clickable="true" focusable="true" '
            b'visible-to-user="true" bounds="[10,20][110,120]"/></hierarchy>'
        )

    def run(
        self,
        *,
        executable: str,
        args: tuple[str, ...],
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> Result[AdbCommandResult, AgentXError]:
        del timeout_seconds, max_output_bytes
        self.calls.append(args)
        stdout = b""
        stderr = b""
        returncode = 0
        if args == ("devices", "-l"):
            stdout = ("List of devices attached\n" + self.devices_output).encode()
        elif "shell" in args:
            command = args[-1]
            if command == "getprop ro.build.version.release":
                stdout = b"14\n"
            elif command == "getprop ro.product.model":
                stdout = b"Pixel Test\n"
            elif command == "getprop ro.build.version.sdk":
                stdout = b"34\n"
            elif command == "pm list packages":
                stdout = b"package:com.example.app\npackage:com.example.other\n"
            elif command.startswith("cmd package resolve-activity --brief "):
                stdout = b"com.example.app/.MainActivity\n"
            elif command.startswith("am start -W -n "):
                stdout = b"Status: ok\n"
            elif command == "dumpsys activity activities":
                stdout = (
                    f"mResumedActivity: ActivityRecord{{x u0 {self.foreground}/.MainActivity t1}}\n"
                ).encode()
            elif command.startswith("uiautomator dump "):
                stdout = b"UI hierchary dumped\n"
            elif command.startswith("input "):
                stdout = b""
            else:
                returncode = 1
                stderr = b"unexpected fake shell command"
        elif "exec-out" in args:
            command = args[-1]
            if command.startswith("cat "):
                stdout = self.ui_xml
            elif command == "screencap -p":
                stdout = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x04\x00\x00\x00\x08\x00"
            else:
                returncode = 1
                stderr = b"unexpected fake exec-out command"
        return Result.success(
            AdbCommandResult(
                argv=(executable, *args),
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        )


def context() -> ExecutionContext:
    source = CancellationSource()
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)


def test_adb_remote_shell_quotes_hostile_text_as_data() -> None:
    runner = FakeAdbRunner()
    transport = AdbTransport(runner=runner)
    payload = "hello; touch /data/local/tmp/pwn"
    result = transport.shell(
        "emulator-5554",
        ("input", "text", payload),
        context=context(),
    )
    assert result.is_success
    remote = runner.calls[-1][-1]
    assert remote.startswith("input text ")
    assert "'hello; touch /data/local/tmp/pwn'" in remote
    assert runner.calls[-1][:3] == ("-s", "emulator-5554", "shell")


def test_ui_semantic_resolution_fails_closed_on_ambiguity() -> None:
    payload = (
        b"<hierarchy>"
        b'<node resource-id="id/a" text="ALLOW ADMIN" content-desc="" class="Button" '
        b'package="com.example.app" enabled="true" clickable="true" focusable="true" '
        b'bounds="[0,0][10,10]"/>'
        b'<node resource-id="id/b" text="ALLOW ADMIN" content-desc="" class="Button" '
        b'package="com.example.app" enabled="true" clickable="true" focusable="true" '
        b'bounds="[20,0][30,10]"/>'
        b"</hierarchy>"
    )
    tree = parse_android_ui_tree(payload)
    selector = AndroidTargetSelector(text="ALLOW ADMIN", require_clickable=True)
    with pytest.raises(AndroidUiValidationError, match="ambiguous"):
        resolve_android_target(tree, selector)


def test_android_provider_registry_health_restart_and_routing() -> None:
    runner = FakeAdbRunner()
    provider = AndroidProvider(AdbTransport(runner=runner))
    discovered = provider.discover(context=context(), observed_at=_T0)
    assert discovered.is_success
    descriptor = discovered.unwrap()[0]
    assert descriptor.platform is DevicePlatform.ANDROID

    registry = DeviceRegistry()
    registry.observe(descriptor)
    assert (
        evaluate_device_health(descriptor, now=_T0, max_age=timedelta(seconds=30))
        is DeviceHealth.HEALTHY
    )

    capability = descriptor.capabilities[0].capability
    routed = DeviceRouter(registry).select(
        DeviceRequirement(
            capability=capability,
            platform=DevicePlatform.ANDROID,
            device_id=descriptor.device_id,
        ),
        now=_T0,
        max_age=timedelta(seconds=30),
    )
    assert routed.is_success
    assert routed.unwrap().device_id == descriptor.device_id

    restarted = DeviceRegistry.restore(registry.snapshot())
    assert restarted.available(now=_T0, max_age=timedelta(seconds=30)) == ()


def test_android_capabilities_are_android_scoped_and_independently_verify() -> None:
    runner = FakeAdbRunner()
    capabilities = build_android_capabilities(AdbTransport(runner=runner))
    assert {cap.descriptor.scope.platform for cap in capabilities} == {CapabilityPlatform.ANDROID}

    package_capability = next(
        cap
        for cap in capabilities
        if cap.descriptor.identity.name.value == "android.package_discovery"
    )
    request = CapabilityRequest(
        identity=package_capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.PACKAGE_DISCOVERY,
            serial="emulator-5554",
        ),
    )
    execution = package_capability.execute(request, context())
    assert execution.succeeded
    verification = package_capability.verify(
        request,
        execution.observation,
        context(),
    )
    assert verification.passed


def test_android_screenshot_is_bounded_png_evidence() -> None:
    runner = FakeAdbRunner()
    capabilities = build_android_capabilities(AdbTransport(runner=runner))
    screen_capability = next(
        cap
        for cap in capabilities
        if cap.descriptor.identity.name.value == "android.screen_capture"
    )
    request = CapabilityRequest(
        identity=screen_capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.SCREEN_CAPTURE,
            serial="emulator-5554",
        ),
    )
    execution = screen_capability.execute(request, context())
    assert execution.succeeded
    assert execution.observation.data["width"] == 1024
    assert execution.observation.data["height"] == 2048
    assert screen_capability.verify(request, execution.observation, context()).passed


def _capability_by_name(runner: FakeAdbRunner, name: str):
    return next(
        cap
        for cap in build_android_capabilities(AdbTransport(runner=runner))
        if cap.descriptor.identity.name.value == name
    )


def test_android_app_launch_requires_independent_foreground_readback() -> None:
    runner = FakeAdbRunner()
    capability = _capability_by_name(runner, "android.app_launch")
    request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.APP_LAUNCH,
            serial="emulator-5554",
            package="com.example.app",
        ),
    )
    execution = capability.execute(request, context())
    assert execution.succeeded
    assert execution.observation.data["component"] == "com.example.app/.MainActivity"
    assert capability.verify(request, execution.observation, context()).passed

    runner.foreground = "com.example.other"
    rejected = capability.verify(request, execution.observation, context())
    assert rejected.passed is False


def test_android_semantic_tap_uses_unique_target_center_and_verifies_state() -> None:
    runner = FakeAdbRunner()
    capability = _capability_by_name(runner, "android.tap")
    selector = AndroidTargetSelector(
        resource_id="com.example:id/go",
        package="com.example.app",
        require_clickable=True,
    )
    request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.TAP,
            serial="emulator-5554",
            selector=selector,
            verification=AndroidVerificationSpec(target_present=selector),
        ),
    )
    execution = capability.execute(request, context())
    assert execution.succeeded
    assert execution.observation.data["x"] == 60
    assert execution.observation.data["y"] == 70
    assert any(call[-1] == "input tap 60 70" for call in runner.calls)
    assert capability.verify(request, execution.observation, context()).passed


def test_android_text_swipe_back_and_home_are_typed_and_verified() -> None:
    runner = FakeAdbRunner()
    verification = AndroidVerificationSpec(expected_foreground_package="com.example.app")

    text_capability = _capability_by_name(runner, "android.text_entry")
    text_request = CapabilityRequest(
        identity=text_capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.TEXT_ENTRY,
            serial="emulator-5554",
            text="hello world",
            verification=verification,
        ),
    )
    text_execution = text_capability.execute(text_request, context())
    assert text_execution.succeeded
    assert text_execution.observation.data["text"] == "[REDACTED]"
    assert text_capability.verify(text_request, text_execution.observation, context()).passed

    swipe_capability = _capability_by_name(runner, "android.swipe")
    swipe_request = CapabilityRequest(
        identity=swipe_capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.SWIPE,
            serial="emulator-5554",
            x=10,
            y=20,
            x2=30,
            y2=40,
            duration_ms=250,
            verification=verification,
        ),
    )
    swipe_execution = swipe_capability.execute(swipe_request, context())
    assert swipe_execution.succeeded
    assert swipe_capability.verify(
        swipe_request,
        swipe_execution.observation,
        context(),
    ).passed

    navigation_capability = _capability_by_name(runner, "android.navigation")
    for navigation, expected_keycode in (
        (AndroidNavigation.BACK, "4"),
        (AndroidNavigation.HOME, "3"),
    ):
        navigation_request = CapabilityRequest(
            identity=navigation_capability.descriptor.identity,
            params=AndroidActionParams(
                operation=AndroidOperation.NAVIGATION,
                serial="emulator-5554",
                navigation=navigation,
                verification=verification,
            ),
        )
        execution = navigation_capability.execute(navigation_request, context())
        assert execution.succeeded
        assert any(call[-1] == f"input keyevent {expected_keycode}" for call in runner.calls)
        assert navigation_capability.verify(
            navigation_request,
            execution.observation,
            context(),
        ).passed


def test_mutating_android_actions_without_postcondition_do_not_self_verify() -> None:
    runner = FakeAdbRunner()
    capability = _capability_by_name(runner, "android.tap")
    request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.TAP,
            serial="emulator-5554",
            x=50,
            y=60,
        ),
    )
    execution = capability.execute(request, context())
    assert execution.succeeded
    verification = capability.verify(request, execution.observation, context())
    assert verification.passed is False
    assert "no independent postcondition" in verification.detail
