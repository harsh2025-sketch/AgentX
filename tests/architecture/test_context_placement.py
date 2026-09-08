"""Architecture proof for the inward, data-only M2.05 context envelope."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import get_args

from agentx.core.context import (
    AgentContext,
    ContextItem,
    ContextItemKind,
    ContextLimits,
    ContextRecord,
)

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "core" / "context.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "context dependencies must be explicit absolute imports"
            assert node.module is not None
            result.add(node.module)
    return result


def _calls() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                result.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                result.add(node.func.attr)
    return result


def test_context_contract_lives_only_in_core() -> None:
    assert _MODULE.is_file()
    for contract in (AgentContext, ContextItem, ContextItemKind, ContextLimits):
        assert contract.__module__ == "agentx.core.context"
    owners = [
        path.relative_to(_ROOT / "src")
        for path in (_ROOT / "src").rglob("*.py")
        if any(
            isinstance(node, ast.ClassDef) and node.name == "AgentContext"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert owners == [Path("agentx/core/context.py")]


def test_imports_are_exclusively_stdlib_and_canonical_core_contracts() -> None:
    imports = _imports(_tree())
    assert "agentx.core.ids" in imports
    assert "agentx.core.knowledge" in imports
    for name in imports:
        if name.startswith("agentx"):
            assert name.startswith("agentx.core."), name
        else:
            assert name.split(".")[0] in sys.stdlib_module_names, name
    assert not imports & {"agentx._architecture", "agentx.agent_loop"}


def test_import_does_not_load_outward_subsystems_even_indirectly() -> None:
    # Fresh process: already-imported modules from unrelated tests cannot mask
    # a transitive core -> runtime/Hive/cognition dependency.
    script = """
import json
import sys
import agentx.core.context
outward = ('agentx.hive', 'agentx.cognition', 'agentx.kernel',
           'agentx.capabilities', 'agentx.learning', 'agentx.procedures',
           'agentx.infrastructure', 'agentx.agent_loop')
print(json.dumps(sorted(name for name in sys.modules
                       if any(name == prefix or name.startswith(prefix + '.')
                              for prefix in outward))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(completed.stdout) == []


def test_evidence_types_are_reused_not_redeclared_or_flattened() -> None:
    record_types = get_args(ContextRecord.__value__)
    assert len(record_types) == len(ContextItemKind) == 7
    assert all(
        record.__module__.startswith("agentx.core.") and record.__module__ != "agentx.core.context"
        for record in record_types
    )
    declarations = {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}
    assert declarations.isdisjoint(
        {
            "Task",
            "TaskId",
            "KnowledgeId",
            "EpisodeId",
            "ProcedureId",
            "NegativeExperienceId",
            "KnowledgeRecord",
            "EpisodeRecord",
            "NegativeExperienceRecord",
            "CausalExperience",
            "ProcedureRecord",
            "KnowledgeStatus",
            "ProcedureStatus",
            "KnowledgeScope",
            "ProcedureScope",
            "ProvenanceReference",
            "EvidenceReference",
            "AuthorityContext",
            "Permission",
            "RiskLevel",
            "ActionGate",
            "ResourceEnvelope",
            "EmergencyStop",
        }
    )


def test_no_retrieval_model_prompt_tokenizer_storage_or_side_effect_imports() -> None:
    roots = {name.split(".")[0] for name in _imports(_tree())}
    assert roots.isdisjoint(
        {
            "os",
            "io",
            "pathlib",
            "subprocess",
            "socket",
            "sqlite3",
            "pickle",
            "marshal",
            "shelve",
            "http",
            "urllib",
            "requests",
            "httpx",
            "aiohttp",
            "importlib",
            "multiprocessing",
            "asyncio",
            "ctypes",
            "openai",
            "anthropic",
            "transformers",
            "tiktoken",
            "tokenizers",
            "jinja2",
            "langchain",
        }
    )


def test_no_execution_retrieval_prompt_or_authority_calls() -> None:
    assert _calls().isdisjoint(
        {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "asdict",
            "deepcopy",
            "query",
            "search",
            "retrieve",
            "recall",
            "recall_all",
            "history",
            "read",
            "execute",
            "activate",
            "promote",
            "transition",
            "grant",
            "permit",
            "reset",
            "publish",
            "emit",
            "reason",
            "invoke",
            "complete",
            "build_prompt",
            "tokenize",
            "connect",
            "write_text",
            "write_bytes",
            "rank",
            "observe",
            "is_fresh",
        }
    )


def test_json_decoders_have_no_object_construction_or_caller_supplied_hooks() -> None:
    for node in ast.walk(_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"loads", "load", "JSONDecoder"}
        ):
            assert not node.keywords


def test_public_envelope_fields_have_no_authority_success_or_global_trust_surface() -> None:
    assert {definition.name for definition in fields(AgentContext)} == {
        "task_id",
        "correlation_id",
        "created_at",
        "items",
        "scope",
        "metadata",
        "limits",
        "schema_version",
    }
    assert {definition.name for definition in fields(ContextItem)} == {
        "record",
        "record_reference",
        "kind",
    }
    assert {definition.name for definition in fields(ContextLimits)} == {
        "max_items",
        "max_total_bytes",
        "max_item_bytes",
        "max_metadata_entries",
        "max_metadata_bytes",
    }
    for record in (AgentContext, ContextItem, ContextLimits):
        assert record.__dict__["__dataclass_params__"].frozen
        assert "__slots__" in record.__dict__


def test_context_adds_no_store_provider_or_runtime_port() -> None:
    declarations = {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}
    assert not any(
        name.endswith(("Store", "Provider", "Port", "Engine", "Manager", "Executor", "Reasoner"))
        for name in declarations
    )
    definitions = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not definitions & {
        "retrieve",
        "rank",
        "reason",
        "execute",
        "persist",
        "observe",
        "activate",
        "promote",
        "grant",
        "reset",
        "build_prompt",
    }
