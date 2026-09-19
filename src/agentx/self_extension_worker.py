"""Isolated worker for M15 safe generated-tool evaluation.

This module is intentionally tiny: it accepts one JSON envelope on stdin, invokes
the trusted safe-subset interpreter, and writes one JSON result on stdout. Candidate
source is never exec/eval/imported.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping

from agentx.self_extension import SelfExtensionSecurityError, execute_safe_candidate


def main() -> int:
    try:
        raw = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("sandbox envelope must be an object")
        source = raw.get("source")
        payload = raw.get("payload")
        max_steps = raw.get("max_steps")
        if not isinstance(source, str) or not isinstance(payload, Mapping) or type(max_steps) is not int:
            raise ValueError("sandbox envelope is malformed")
        output = execute_safe_candidate(
            source=source,
            payload=payload,
            max_steps=max_steps,
        )
        encoded = json.dumps(
            {"ok": True, "output": output},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        sys.stdout.write(encoded)
        return 0
    except (
        SelfExtensionSecurityError,
        ValueError,
        TypeError,
        KeyError,
        ArithmeticError,
        OverflowError,
    ) as exc:
        encoded = json.dumps(
            {"ok": False, "error": type(exc).__name__},
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write(encoded)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
