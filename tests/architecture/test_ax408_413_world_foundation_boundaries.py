"""Authority and ownership guards for AX-408/410/412/413 completion seams."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODULES = (
    _ROOT / "src/agentx/core/environment_freshness.py",
    _ROOT / "src/agentx/capabilities/browser_dom_invalidation.py",
)


def test_completion_seams_do_not_import_runtime_authority_owners() -> None:
    forbidden = (
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.capabilities.runtime",
        "agentx.capabilities.registry",
        "agentx.executor",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in _MODULES)
    assert all(token not in source for token in forbidden)


def test_freshness_and_dom_invalidation_modules_define_no_background_runtime() -> None:
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in _MODULES)
    forbidden = ("threading", "asyncio.create_task", "while true", "subprocess", "powershell")
    assert all(token not in source for token in forbidden)
