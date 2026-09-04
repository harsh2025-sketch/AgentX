"""Command-line entry point for AgentX.

Invoked as ``python -m agentx`` or through the ``agentx`` console script
declared in ``pyproject.toml``.

Bootstrap scope: only ``--version`` and ``--help`` are supported. No agent
commands exist yet, and a full command framework is intentionally not built
until a task calls for one. Plain :mod:`argparse` is sufficient here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agentx import __version__

PROG = "agentx"
"""Program name shown in usage/help/version output (stable across platforms)."""


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the AgentX command-line interface."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "AgentX command-line interface (bootstrap). No agent commands are available yet."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="print the AgentX version and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AgentX command-line interface and return a process exit code.

    Args:
        argv: Command-line arguments *excluding* the program name. When
            ``None`` (the default), ``sys.argv[1:]`` is used.

    Returns:
        ``0`` on success. ``--version`` and ``--help`` are handled by
        :mod:`argparse`, which prints to stdout and raises ``SystemExit(0)``;
        invalid arguments print usage to stderr and raise ``SystemExit(2)``.
    """
    parser = build_parser()
    parser.parse_args(argv)
    # There are no sub-commands during bootstrap. Rather than exit silently,
    # a bare invocation shows the help text so the user learns what exists.
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
