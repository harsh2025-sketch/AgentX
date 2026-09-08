import pytest

from agentx.capabilities.windows.application_launch_v2 import (
    ApplicationLaunchParams,
    NativeLaunchResult,
)


def test_structured_request_is_bounded_and_preserved() -> None:
    request = ApplicationLaunchParams("C:\\app.exe", ("--flag", "value"))
    assert request.argv == ("--flag", "value")


@pytest.mark.parametrize("value", ["", "bad\x00path"])
def test_executable_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        ApplicationLaunchParams(value, ())


def test_argv_nul_rejected() -> None:
    with pytest.raises(ValueError):
        ApplicationLaunchParams("app.exe", ("x\x00",))


def test_native_pid_is_evidence_only() -> None:
    assert NativeLaunchResult(True, 42).process_id == 42
    with pytest.raises(ValueError):
        NativeLaunchResult(True, 0)
