"""Test-only ChromeDriver service lifecycle for real-browser acceptance.

Production browser code never launches processes. Canonical CI owns this
trusted fixture lifecycle and supplies the resulting loopback endpoint to the
production W3C WebDriver provider.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ChromeDriverService:
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
