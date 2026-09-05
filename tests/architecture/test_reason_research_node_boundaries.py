"""Architecture/security guards for A3.04 REASON and RESEARCH contracts."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.cognition.model_roles import ModelRole
from agentx.procedures.reason_research import ReasonNodeSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "procedures" / "reason_research.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _defined_class_names() -> set[str]:
    return {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}


def _called_attribute_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _referenced_names() -> set[str]:
    return {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)}


def test_reason_role_token_is_pinned_to_canonical_a2_02_model_role() -> None:
    spec = ReasonNodeSpec(objective="reason", output_binding="out")
    assert spec.logical_model_role == ModelRole.REASONING.value


def test_production_module_preserves_procedure_dependency_direction() -> None:
    imported = _imports()
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports == {"agentx.procedures.graph"}

    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.hive",
        "agentx.learning",
    )
    assert not any(module.startswith(forbidden_prefixes) for module in imported)


def test_no_reasoner_model_provider_or_runtime_invocation_surface() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    classes = _defined_class_names()
    calls = _called_attribute_names()

    assert "Reasoner" not in classes
    assert "ModelProvider" not in classes
    assert "ReasonerRequest" not in source
    assert "ModelRequest" not in source
    assert "ModelResponse" not in source
    assert calls.isdisjoint(
        {
            "reason",
            "generate",
            "complete",
            "invoke",
            "execute",
            "verify",
            "transition",
        }
    )


def test_no_network_browser_or_filesystem_research_surface() -> None:
    imported = _imports()
    forbidden_imports = {
        "requests",
        "urllib",
        "urllib.request",
        "http",
        "http.client",
        "socket",
        "webbrowser",
        "pathlib",
        "subprocess",
    }
    assert imported.isdisjoint(forbidden_imports)

    calls = _called_attribute_names()
    assert calls.isdisjoint(
        {
            "open",
            "read_text",
            "write_text",
            "read_bytes",
            "write_bytes",
            "urlopen",
            "get",
            "post",
            "request",
        }
    )


def test_contract_defines_no_chain_of_thought_verification_or_authority_types() -> None:
    referenced = _referenced_names()
    forbidden_symbols = {
        "AuthorityContext",
        "Permission",
        "ActionGate",
        "RiskAssessment",
        "ResourceEnvelope",
        "ResourceBudget",
        "EmergencyStop",
        "KnowledgeStatus",
        "TaskState",
        "Capability",
        "ProcedureStatus",
    }
    assert referenced.isdisjoint(forbidden_symbols)

    class_names = _defined_class_names()
    forbidden_contracts = {
        "ChainOfThought",
        "ReasoningTrace",
        "VerificationResult",
        "ResearchProvider",
        "ResearchResult",
        "AuthorityGrant",
    }
    assert class_names.isdisjoint(forbidden_contracts)


def test_a3_02_a3_03_and_a3_05_node_semantics_are_not_defined_here() -> None:
    names = _defined_class_names()
    forbidden = {
        "ActionNodeSpec",
        "ObserveNodeSpec",
        "VerifyNodeSpec",
        "BranchNodeSpec",
        "TransformNodeSpec",
        "WaitNodeSpec",
        "RollbackNodeSpec",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
    }
    assert names.isdisjoint(forbidden)


def test_no_new_runtime_dependency_surface() -> None:
    imported = _imports()
    allowed_stdlib = {
        "__future__",
        "json",
        "collections.abc",
        "dataclasses",
        "typing",
    }
    non_agentx = {module for module in imported if not module.startswith("agentx.")}
    assert non_agentx <= allowed_stdlib
