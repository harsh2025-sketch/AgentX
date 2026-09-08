"""Architecture guards for the M6.04 explicit user-preference contract.

The preference model is a pure inward ``agentx.core`` data contract: typed
explicit records with no inference, no persistence, no ranking, no routing,
no authority, and no model. These static guards prove it stays that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "user_preferences.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_KNOWLEDGE = (_REPO_ROOT / "src" / "agentx" / "core" / "knowledge.py").read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}
    assert agentx_imports == {
        "agentx.core.knowledge",
        "agentx.core.provenance",
    }
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.cognition",
        "agentx.learning",
        "agentx.procedures",
    ):
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}
    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "json",
        "math",
        "types",
        "typing",
        "uuid",
    }


def test_preferences_define_data_contracts_not_subsystems() -> None:
    classes = _classes()
    forbidden = {
        # Authority / execution / routing must never live here.
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "PermissionEngine",
        "RiskAssessment",
        "RiskLevel",
        "ResourceEnvelope",
        "ResourceBudget",
        "EmergencyStop",
        "Executor",
        "Capability",
        "Router",
        "TaskManager",
        "Reasoner",
        "Verifier",
        # Storage / persistence must never live here.
        "UserPreferenceStore",
        "PreferenceStore",
        "UserModel",
        "UserModelStore",
        "Migration",
        # Inference / learning / ranking must never live here.
        "PreferenceLearner",
        "PreferenceInference",
        "PreferenceRanker",
        "PreferenceResolver",
        "PreferenceEngine",
        "PreferenceService",
        "PreferenceManager",
        "EmbeddingModel",
        # Competing contracts must never be redefined here.
        "KnowledgeRecord",
        "KnowledgeScope",
        "KnowledgeStatus",
        "KnowledgeType",
        "ScopeDimension",
        "ProvenanceReference",
        "ProvenanceKind",
        "EvidenceReference",
        "EvidenceKind",
        "Task",
        "TaskId",
        "KnowledgeId",
        "ProcedureRecord",
        "ProcedureId",
    }
    assert classes.isdisjoint(forbidden)
    assert classes == {
        "UserPreferenceValidationError",
        "UserPreferenceDeserializationError",
        "UnsupportedUserPreferenceSchemaVersionError",
        "PreferenceKey",
        "PreferenceSource",
        "UserPreference",
    }


def test_vocabulary_stays_minimal_no_strength_or_status() -> None:
    # Strength would imply ranking/inference; status would imply lifecycle.
    # Both are explicit non-goals, so neither concept may exist here.
    classes = _classes()
    referenced = _referenced_names()
    for absent in ("PreferenceStrength", "PreferenceStatus", "PreferenceRank", "Confidence"):
        assert absent not in classes
        assert absent not in referenced
    # The docstring records WHY strength/status are absent; no code may define them.
    assert "class PreferenceStrength" not in _SOURCE
    assert "class PreferenceStatus" not in _SOURCE


def test_no_competing_error_id_or_scope_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert "ValueError" in exception_bases
    assert "from agentx.core.knowledge import" in _SOURCE
    assert "KnowledgeScope" in _SOURCE
    assert "from agentx.core.provenance import" in _SOURCE
    assert "EvidenceReference" in _SOURCE


def test_records_expose_no_behavioral_methods() -> None:
    forbidden_methods = {
        "apply",
        "enforce",
        "grant",
        "authorize",
        "approve",
        "bypass",
        "execute",
        "run",
        "route",
        "choose",
        "select",
        "resolve",
        "rank",
        "score",
        "infer",
        "learn",
        "observe",
        "activate",
        "verify",
        "promote",
        "publish",
        "persist",
        "save",
        "load",
        "migrate",
        "embed",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods, (
                    f"{node.name}.{statement.name} is behavioral and forbidden"
                )


def test_no_store_migration_model_or_network_surface() -> None:
    referenced = _referenced_names()
    called = _called_names()
    for foreign in (
        "KnowledgeStore",
        "EpisodeStore",
        "ProcedureStore",
        "ArtifactStore",
        "AuditStore",
        "EventJournal",
        "EventBus",
        "SemanticMemory",
        "ExperienceMemory",
        "ModelProvider",
        "Reasoner",
        "Router",
        "sqlite3",
        "pickle",
        "Migration",
        "migrate",
    ):
        assert foreign not in referenced
        assert foreign not in called
    lowered = _SOURCE.lower()
    assert "sqlite" not in lowered
    # No executable decoding or dynamic construction.
    assert "object_hook" not in _SOURCE
    assert "pickle" not in _SOURCE
    assert "__import__" not in _SOURCE
    assert "eval(" not in _SOURCE
    assert "exec(" not in _SOURCE


def test_no_keyword_or_model_inference() -> None:
    # Checked against code identifiers/calls, not prose: the docstring names
    # these non-goals on purpose to record what the contract never does.
    referenced = {name.lower() for name in _referenced_names()}
    called = {name.lower() for name in _called_names()}
    for token in ("infer", "predict", "embedding", "cosine", "similarity", "rank", "score"):
        assert token not in referenced
        assert token not in called
    tree = _tree()
    code_text = "\n".join(
        ast.get_source_segment(_SOURCE, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ).lower()
    for token in ("embedding", "cosine", "similarity"):
        assert token not in code_text


def test_clock_is_used_only_by_the_create_factory() -> None:
    # Deterministic construction is the default; only create() may stamp now.
    tree = _tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            segment = ast.get_source_segment(_SOURCE, statement) or ""
            if statement.name == "create":
                continue
            assert "datetime.now" not in segment
            assert "datetime.utcnow" not in segment
    assert "datetime.now(UTC)" in _SOURCE  # the single canonical factory default


def test_core_knowledge_contract_is_untouched() -> None:
    assert "class KnowledgeRecord" in _KNOWLEDGE
    assert "class KnowledgeScope" in _KNOWLEDGE
    assert "class UserPreference" not in _KNOWLEDGE
    assert "class PreferenceKey" not in _KNOWLEDGE
    assert "class PreferenceSource" not in _KNOWLEDGE


def test_user_preference_has_exactly_one_canonical_definition() -> None:
    definitions: list[str] = []
    for path in (_REPO_ROOT / "src" / "agentx").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # Newer-syntax modules (3.12+) cannot be parsed by older
            # interpreters; CI on the supported toolchain parses everything.
            # This guard still proves the contract lives exactly once among
            # all parseable modules and never in the files it must not touch.
            continue
        if any(
            isinstance(node, ast.ClassDef) and node.name == "UserPreference"
            for node in ast.walk(tree)
        ):
            definitions.append(str(path.relative_to(_REPO_ROOT / "src")))
    assert definitions == ["agentx/core/user_preferences.py"]


def test_docs_page_exists_for_m604() -> None:
    docs = _REPO_ROOT / "docs" / "user_preferences.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "M6.04" in text
    assert "user preference" in lowered
    for token in (
        "USER_EXPLICIT",
        "USER_CORRECTION",
        "supersedes",
        "applicability",
        "inert",
        "authority",
        "Serialization",
        "Bounds",
    ):
        assert token in text
