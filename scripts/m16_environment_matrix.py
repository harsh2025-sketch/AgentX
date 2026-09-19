"""Capture truthful M16 host-matrix evidence without platform simulation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def collect_environment() -> dict[str, Any]:
    """Return bounded host evidence and conservative desktop-matrix verdicts."""
    system = platform.system()
    release = platform.release()
    version = platform.version()
    edition = ""
    build = 0
    product_type: int | None = None
    monitor_count: int | None = None
    system_dpi: int | None = None

    if system == "Windows":
        edition_fn = getattr(platform, "win32_edition", None)
        if callable(edition_fn):
            edition = str(edition_fn())
        windows_version = sys.getwindowsversion()
        build = int(windows_version.build)
        product_type = int(windows_version.product_type)

        try:
            import ctypes

            user32 = ctypes.windll.user32
            monitor_count = int(user32.GetSystemMetrics(80))
            get_dpi = getattr(user32, "GetDpiForSystem", None)
            if get_dpi is not None:
                system_dpi = int(get_dpi())
        except (AttributeError, OSError, TypeError, ValueError):
            monitor_count = None
            system_dpi = None

    workstation = system == "Windows" and product_type == 1 and "server" not in edition.lower()
    windows10 = workstation and 10_240 <= build < 22_000
    windows11 = workstation and build >= 22_000

    return {
        "schema_version": 1,
        "platform": {
            "system": system,
            "release": release,
            "version": version,
            "edition": edition,
            "build": build,
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "product_type": product_type,
        },
        "ci": {
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "runner_name": os.environ.get("RUNNER_NAME"),
            "runner_os": os.environ.get("RUNNER_OS"),
        },
        "observations": {
            "monitor_count": monitor_count,
            "system_dpi": system_dpi,
        },
        "matrix": {
            "windows_10_workstation_proven": windows10,
            "windows_11_workstation_proven": windows11,
            "multi_monitor_observed": monitor_count is not None and monitor_count >= 2,
            "multi_dpi_proven": False,
        },
        "limitations": [
            "Windows Server is not accepted as Windows 10/11 workstation evidence.",
            "A single system-DPI observation is not a multi-DPI matrix.",
            "Monitor enumeration on hosted CI is not claimed as physical multi-monitor evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = collect_environment()
    encoded = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
