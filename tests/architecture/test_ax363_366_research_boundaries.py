"""Architecture guards for the AX-363-366 research campaign slice."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GAP = _REPO / "src" / "agentx" / "cognition" / "research_gap_state.py"
_LOOKUP = _REPO / "src" / "agentx" / "hive_first_research.py"
_INGESTION = _REPO / "src" / "agentx" / "research_ingestion.py"
_MODULES = (_GAP, _LOOKUP, _INGESTION)
_FORBIDDEN_PREFIXES = (
    "agentx.kernel",
    "agentx.capabilities.runtime",
    "agentx.executor",
    "agentx.agent_loop",
    "agentx.cognition.reasoner",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_research_gap_lookup_and_ingestion_cannot_import_authority_or_execution_owners() -> None:
    for path in _MODULES:
        for imported in _imports(path):
            assert not any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in _FORBIDDEN_PREFIXES
            ), f"{path.name} crosses trusted execution boundary via {imported}"


def test_cognition_research_classifier_has_no_outward_infrastructure_dependency() -> None:
    imports = _imports(_GAP)
    assert not any(
        imported == "agentx.infrastructure" or imported.startswith("agentx.infrastructure.")
        for imported in imports
    )
    cognition_dir = _REPO / "src" / "agentx" / "cognition"
    assert not (cognition_dir / "hive_first_research.py").exists()
    assert not (cognition_dir / "research_ingestion.py").exists()


def test_research_campaign_modules_are_bounded_explicit_composition_surfaces() -> None:
    gap = _GAP.read_text(encoding="utf-8")
    lookup = _LOOKUP.read_text(encoding="utf-8")
    ingestion = _INGESTION.read_text(encoding="utf-8")

    assert "MAX_RESEARCH_GAP_RECORDS" in gap
    assert "MAX_HIVE_FIRST_LOOKUP_IDS" in lookup
    assert "KnowledgeRecord.create" in ingestion
    assert "update_status" not in ingestion
    assert "CapabilityExecutionLoop" not in lookup
    assert "CapabilityExecutionLoop" not in ingestion
