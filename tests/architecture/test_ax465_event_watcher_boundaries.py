"""Authority/daemon guards for AX-465 watcher framework."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = (_ROOT / "src/agentx/infrastructure/event_watcher.py").read_text(encoding="utf-8")


def test_watcher_framework_does_not_own_machine_authority() -> None:
    forbidden = (
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.capabilities.runtime",
        "agentx.capabilities.registry",
        "agentx.executor",
    )
    assert all(token not in _SOURCE for token in forbidden)


def test_watcher_framework_has_no_unbounded_background_loop() -> None:
    lowered = _SOURCE.lower()
    forbidden = (
        "while true",
        "threading.thread",
        "asyncio.create_task",
        "schedule.every",
        "daemon=true",
    )
    assert all(token not in lowered for token in forbidden)
