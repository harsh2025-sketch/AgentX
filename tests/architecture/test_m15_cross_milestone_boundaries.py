from __future__ import annotations

import ast
from pathlib import Path

from agentx.self_extension import (
    CandidateSourceType,
    CapabilityGapReason,
)


ROOT = Path(__file__).resolve().parents[2]
MILESTONE_MODULES = (
    "src/agentx/adaptive_optimization.py",
    "src/agentx/device_orchestration.py",
    "src/agentx/scheduling.py",
    "src/agentx/voice_runtime.py",
    "src/agentx/research_ingestion.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_m11_m12_m13_m14_and_untrusted_research_cannot_directly_promote_extensions() -> None:
    for relative in MILESTONE_MODULES:
        imports = _imports(ROOT / relative)
        assert "agentx.self_extension" not in imports


def test_extension_sources_are_data_not_authority() -> None:
    assert CandidateSourceType.MODEL_ASSISTED.value == "model_assisted"
    assert CandidateSourceType.RESEARCH_ASSISTED.value == "research_assisted"
    assert CandidateSourceType.SOURCE_REPOSITORY.value == "source_repository"
    assert CandidateSourceType.PACKAGE.value == "package"
    assert CapabilityGapReason.PERMISSION_DENIED.extension_eligible is False
    assert CapabilityGapReason.RISK_REJECTED.extension_eligible is False
    assert CapabilityGapReason.BUDGET_EXHAUSTED.extension_eligible is False
    assert CapabilityGapReason.CANCELLED.extension_eligible is False
    assert CapabilityGapReason.EMERGENCY_STOP.extension_eligible is False
