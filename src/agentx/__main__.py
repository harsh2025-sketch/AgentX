"""Command-line entry point for the AgentX release candidate.

The CLI intentionally exposes only bounded release operations. It does not add a
second AgentLoop, a shell/exec surface, implicit permissions, or automatic M15
promotion. Goal execution remains owned by the canonical governed runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agentx import __version__
from agentx.infrastructure.config import ConfigError
from agentx.infrastructure.config_migration import ConfigMigrationError, migrate_config_file
from agentx.infrastructure.persistence import PersistenceError
from agentx.release import ReleaseStartupError, initialize_release_state

PROG = "agentx"


def build_parser() -> argparse.ArgumentParser:
    """Create the bounded release command parser."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="AgentX governed runtime release tooling.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="print the AgentX version and exit",
    )
    commands = parser.add_subparsers(dest="command")

    init_parser = commands.add_parser(
        "init",
        help="initialize canonical configuration/data storage and run recovery checks",
    )
    init_parser.add_argument("--config", type=Path)
    init_parser.add_argument("--data-dir", type=Path)

    doctor_parser = commands.add_parser(
        "doctor",
        help="open canonical state, apply pending DB migrations, and report integrity",
    )
    doctor_parser.add_argument("--config", type=Path)
    doctor_parser.add_argument("--data-dir", type=Path)

    migrate_parser = commands.add_parser(
        "migrate-config",
        help="atomically migrate one AgentX TOML configuration file",
    )
    migrate_parser.add_argument("path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run bounded release commands and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command in {"init", "doctor"}:
            report = initialize_release_state(
                args.config,
                data_dir=args.data_dir,
            )
            payload = {
                "database": str(report.database_path),
                "read_only": report.read_only,
                "recovery": report.recovery.disposition.value,
                "schema_version": report.schema_version,
                "version": __version__,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
            return 0

        if args.command == "migrate-config":
            result = migrate_config_file(args.path)
            payload = {
                "changed": result.changed,
                "from_version": result.from_version,
                "path": str(result.path),
                "to_version": result.to_version,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
            return 0
    except (ConfigError, ConfigMigrationError, PersistenceError, ReleaseStartupError) as exc:
        sys.stderr.write(f"{PROG}: {exc}\n")
        return 2

    parser.error(f"unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
