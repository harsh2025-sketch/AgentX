from agentx.capabilities.windows.application_launch_v2 import APPLICATION_LAUNCH_V2_IDENTITY


def test_launch_identity_is_versioned() -> None:
    assert APPLICATION_LAUNCH_V2_IDENTITY.version.major == 2
