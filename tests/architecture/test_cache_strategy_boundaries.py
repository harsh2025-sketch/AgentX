"""Architecture guardrails for the N2.03 top-level L0 cache strategy adapter."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_ROOT = _REPO_ROOT / "src" / "agentx"
_ADAPTER = _AGENTX_ROOT / "cache_strategy.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.capabilities.abi",
        "agentx.capabilities.runtime",
        "agentx.capabilities.verifier",
        "agentx.cognition.router",
        "agentx.core.environment_change",
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.reuse_efficiency",
        "agentx.core.result",
        "agentx.core.tasks",
    }
)


def _tree(path: Path = _ADAPTER) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _ADAPTER) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def test_adapter_is_exactly_one_top_level_composition_module() -> None:
    assert _ADAPTER.is_file()
    assert _ADAPTER.parent == _AGENTX_ROOT


def test_adapter_imports_only_existing_canonical_evidence_and_strategy_contracts() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}
    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.infrastructure",
        "agentx.hive",
        "agentx.procedures",
        "agentx.models",
        "agentx.research",
        "agentx.capabilities.executor",
        "agentx.capabilities.registry",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in agentx_imports
        for prefix in forbidden_prefixes
    )


def test_adapter_defines_no_shadow_verification_strategy_or_persistence_schema() -> None:
    assert _classes() == {
        "ReusableResultLookup",
        "CacheReuseCandidate",
        "VerifiedCacheStrategy",
    }
    forbidden = {
        "VerificationResult",
        "VerificationPayload",
        "ClosedLoopOutcome",
        "StrategyResult",
        "StrategyRegistry",
        "ExecutionStrategy",
        "EpisodeStore",
        "CacheStore",
        "Database",
        "Permission",
        "RiskLevel",
        "ResourceBudget",
        "EmergencyStop",
    }
    assert not (_classes() & forbidden)


def test_adapter_has_no_execution_model_research_or_procedure_calls() -> None:
    tree = _tree()
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (
        {
            "execute",
            "run",
            "verify",
            "activate",
            "research",
            "generate",
            "complete",
        }
        & called_attributes
    )


def test_lookup_seam_is_read_only_and_adapter_owns_no_cache_store() -> None:
    tree = _tree()
    lookup_protocol = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReusableResultLookup"
    )
    protocol_methods = {
        node.name
        for node in lookup_protocol.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert protocol_methods == {"lookup"}

    source = _ADAPTER.read_text(encoding="utf-8").lower()
    for marker in ("sqlite", "migration", "insert into", "update ", "delete from"):
        assert marker not in source


def test_adapter_does_not_read_wall_clock_or_invent_confidence() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source
    assert "confidence" not in source.lower()
    assert "probability" not in source.lower()


def test_agent_loop_and_architecture_manifest_are_not_hooked_to_cache_adapter() -> None:
    assert "agentx.cache_strategy" not in _imports(_AGENT_LOOP)
    assert "cache_strategy" not in _ARCHITECTURE.read_text(encoding="utf-8")


def test_adapter_reuses_m8_and_c404_instead_of_redefining_scope_or_freshness_types() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")
    assert "ExecutionEfficiencyEvidence" in source
    assert "EnvironmentSnapshot" in source
    assert "RoutingEvidence" in source
    assert "class KnowledgeScope" not in source
    assert "class EnvironmentObservation" not in source
    assert "class ExecutionEfficiencyEvidence" not in source
    assert "class RoutingEvidence" not in source
