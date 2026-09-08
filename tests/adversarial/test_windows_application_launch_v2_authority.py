from agentx.capabilities.windows.application_launch_v2 import WindowsApplicationLaunchV2Capability


def test_module_does_not_offer_shell_or_command_mode() -> None:
    assert not hasattr(WindowsApplicationLaunchV2Capability, "run_command")
