from pathlib import Path


def test_launch_contract_has_no_native_or_shell_imports() -> None:
    source = Path("src/agentx/capabilities/windows/application_launch_v2.py").read_text()
    assert "ctypes" not in source
    assert "subprocess" not in source
    assert "shell=True" not in source
