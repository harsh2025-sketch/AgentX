"""AX-390 threat-model coverage and code-connection guards."""

from __future__ import annotations

from pathlib import Path

from agentx.threat_model import ThreatBoundary, ThreatClass, canonical_threat_model

_ROOT = Path(__file__).resolve().parents[2]


def test_canonical_threat_model_covers_every_declared_boundary_and_threat_class() -> None:
    model = canonical_threat_model()

    assert model.covered_boundaries == frozenset(ThreatBoundary)
    assert model.covered_threats == frozenset(ThreatClass)
    assert len(model.controls) <= 32


def test_every_threat_control_points_to_real_implementation_and_test_paths() -> None:
    model = canonical_threat_model()

    for control in model.controls:
        for relative in (*control.implementation_paths, *control.test_paths):
            target = _ROOT / relative
            assert target.exists(), f"{control.control_id} references missing path: {relative}"


def test_threat_model_is_descriptive_and_does_not_own_runtime_enforcement() -> None:
    source = (_ROOT / "src/agentx/threat_model.py").read_text(encoding="utf-8")

    forbidden_imports = (
        "from agentx.kernel.action_gate import",
        "from agentx.kernel.permissions import",
        "from agentx.capabilities.runtime import",
        "from agentx.capabilities.registry import",
    )
    assert all(token not in source for token in forbidden_imports)
