"""Architecture guardrails for C2.06 episodic + negative memory.

These are import/structure guardrails, not security enforcement. Authority is
owned exclusively by the Trusted Kernel.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_CORE_NEGATIVE = _AGENTX_SRC / "core" / "negative_experience.py"
_NEGATIVE_STORE = _AGENTX_SRC / "infrastructure" / "negative_experience_store.py"
_EXPERIENCE_MEMORY = _AGENTX_SRC / "hive" / "experience_memory.py"

_C2_06_MODULES = (_CORE_NEGATIVE, _NEGATIVE_STORE, _EXPERIENCE_MEMORY)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        if any(
            isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(_tree(path))
        ):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def test_negative_experience_contract_stays_inside_the_core_boundary() -> None:
    assert all(
        not module.startswith("agentx.")
        or module in {"agentx.core.episodes", "agentx.core.ids", "agentx.core.knowledge"}
        for module in _imports(_CORE_NEGATIVE)
    )


def test_negative_experience_store_uses_only_core_and_persistence_foundation() -> None:
    imports = _imports(_NEGATIVE_STORE)

    assert all(
        not module.startswith("agentx.")
        or module
        in {
            "agentx.core.ids",
            "agentx.core.negative_experience",
            "agentx.infrastructure.persistence",
        }
        for module in imports
    )
    assert "agentx.infrastructure.event_bus" not in imports
    assert "agentx.infrastructure.event_journal" not in imports


def test_experience_memory_depends_only_on_canonical_core_contracts() -> None:
    imports = _imports(_EXPERIENCE_MEMORY)

    assert all(
        not module.startswith("agentx.")
        or module
        in {
            "agentx.core.episodes",
            "agentx.core.ids",
            "agentx.core.negative_experience",
        }
        for module in imports
    )
    # The hive -> core edge is the only canonical one for this subsystem.
    assert (_architecture.HIVE, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert all(not module.startswith("agentx.kernel") for module in imports)
    assert all(not module.startswith("agentx.infrastructure") for module in imports)
    assert all(not module.startswith("agentx.cognition") for module in imports)
    assert all(not module.startswith("agentx.procedures") for module in imports)
    assert all(not module.startswith("agentx.learning") for module in imports)
    assert all(not module.startswith("agentx.capabilities") for module in imports)


def test_c2_06_defines_each_new_contract_exactly_once() -> None:
    assert _class_definitions("NegativeExperienceRecord") == [
        Path("agentx/core/negative_experience.py")
    ]
    assert _class_definitions("NegativeExperienceStore") == [
        Path("agentx/infrastructure/negative_experience_store.py")
    ]
    assert _class_definitions("ExperienceMemory") == [Path("agentx/hive/experience_memory.py")]


def test_c2_06_does_not_redefine_canonical_episode_contracts() -> None:
    for name in ("EpisodeRecord", "EpisodeOutcome", "EpisodeStore"):
        for path in _C2_06_MODULES:
            assert not any(
                isinstance(node, ast.ClassDef) and node.name == name
                for node in ast.walk(_tree(path))
            )


def test_c2_06_introduces_no_runtime_dependencies() -> None:
    allowed_stdlib = {
        "json",
        "sqlite3",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "uuid",
        "__future__",
    }
    for path in _C2_06_MODULES:
        for module in _imports(path):
            root = module.split(".")[0]
            assert module in allowed_stdlib or root == "agentx", module


def test_c2_06_does_not_leak_forbidden_subsystem_concepts() -> None:
    forbidden = (
        "embedding",
        "vector",
        "cosine",
        "similarity_score",
        "def rank",
        "def recommend",
        "state_before",
        "state_after",
        "auto_retry",
    )
    for path in _C2_06_MODULES:
        # Docstrings legitimately *name* the forbidden concepts to disclaim
        # them, so only executable code is inspected here.
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value.value = ""
        source = ast.unparse(tree)
        for fragment in forbidden:
            assert fragment not in source, (path.name, fragment)


def test_c2_06_does_not_modify_cognition_kernel_or_procedure_contracts() -> None:
    for path in _C2_06_MODULES:
        imports = _imports(path)
        assert all(
            not module.startswith(prefix)
            for module in imports
            for prefix in ("agentx.kernel", "agentx.cognition", "agentx.procedures")
        )
