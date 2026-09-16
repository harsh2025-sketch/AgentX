"""AX-516 guards: historical performance evidence never becomes policy authority."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PATH = _ROOT / "src/agentx/strategy_performance_evidence.py"


def test_strategy_evidence_has_no_routing_execution_or_kernel_authority_calls() -> None:
    source = _PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    assert not {name for name in imports if name.startswith("agentx.kernel")}
    assert "agentx.capabilities.runtime" not in imports
    assert not ({"route", "execute", "verify", "evaluate", "authorize", "select"} & calls)


def test_ax516_does_not_implement_contextual_bandits_or_policy_updates() -> None:
    source = _PATH.read_text(encoding="utf-8").lower()
    forbidden_definitions = (
        "class contextualbandit",
        "def choose_arm",
        "def update_policy",
        "def reward",
        "epsilon_greedy",
        "thompson_sampling",
    )
    assert all(token not in source for token in forbidden_definitions)
