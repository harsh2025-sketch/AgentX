from agentx.capabilities.windows.application_launch_v2 import ApplicationLaunchParams


def test_windows_request_accepts_optional_working_directory() -> None:
    assert ApplicationLaunchParams("C:\\app.exe", (), "C:\\work").working_directory == "C:\\work"
