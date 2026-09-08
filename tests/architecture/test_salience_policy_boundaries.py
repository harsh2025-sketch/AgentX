"""Architecture guardrails for C6.05 salience/archive policy.

These are import/structure guardrails, not security enforcement. Authority is
owned exclusively by the Trusted Kernel; knowledge lifecycle transitions are
owned by C2.08 through the canonical store.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_SALIENCE_POLICY = _AGENTX_SRC / "hive" / "salience_policy.py"


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


def test_salience_policy_lives_inside_the_hive_boundary() -> None:
    assert _SALIENCE_POLICY.is_file()
    assert _SALIENCE_POLICY.parent.name == "hive"
    assert _SALIENCE_POLICY.parent.parent.name == "agentx"


def test_salience_policy_uses_only_the_canonical_hive_core_edge() -> None:
    imports = _imports(_SALIENCE_POLICY)

    agentx_imports = [module for module in imports if module.startswith("agentx.")]
    assert agentx_imports, "policy must consume canonical core contracts"
    assert all(module.startswith("agentx.core.") for module in agentx_imports), (
        f"salience policy must import only agentx.core contracts; got {agentx_imports}"
    )
    for module in imports:
        for subsystem in _architecture.SUBSYSTEMS:
            if subsystem == "agentx.core":
                continue
            assert not module.startswith(f"{subsystem}."), (
                f"salience policy must not import {subsystem} (found {module})"
            )


def test_salience_policy_never_depends_on_models_or_persistence() -> None:
    imports = _imports(_SALIENCE_POLICY)

    for forbidden in (
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.knowledge_store",
        "sqlite3",
        "socket",
        "urllib.request",
        "http.client",
        "subprocess",
    ):
        assert forbidden not in imports


def test_salience_policy_is_free_of_io_and_dynamic_execution() -> None:
    forbidden_calls = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
        "remove",
        "unlink",
        "rmtree",
        "print",
        "input",
    }
    for node in ast.walk(_tree(_SALIENCE_POLICY)):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, (
                f"salience policy must not call {name!r}; it is a pure decision boundary"
            )


def test_archive_vocabulary_structurally_excludes_deletion() -> None:
    tree = _tree(_SALIENCE_POLICY)
    tier_members: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "SalienceTier"
            and any(isinstance(base, ast.Name) and base.id == "StrEnum" for base in node.bases)
        ):
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    tier_members.update(
                        target.id for target in statement.targets if isinstance(target, ast.Name)
                    )
                elif isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    tier_members.add(statement.target.id)

    assert tier_members == {"ACTIVE", "ARCHIVAL"}, (
        f"SalienceTier must remain exactly active/archival; got {sorted(tier_members)}"
    )


def test_policy_class_holds_only_config_and_clock() -> None:
    tree = _tree(_SALIENCE_POLICY)
    annotations: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SaliencePolicy":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    annotations[statement.target.id] = ast.unparse(statement.annotation)

    assert set(annotations) == {"config", "clock"}, (
        f"SaliencePolicy must hold exactly its configuration and clock; got {sorted(annotations)}"
    )


def test_reason_codes_are_declared_for_every_evidence_factor() -> None:
    tree = _tree(_SALIENCE_POLICY)
    reason_members: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "SalienceReasonCode"
            and any(isinstance(base, ast.Name) and base.id == "StrEnum" for base in node.bases)
        ):
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    reason_members.update(
                        target.id for target in statement.targets if isinstance(target, ast.Name)
                    )

    # recency, reuse, verification, negative importance, supersession, scope/
    # environment, provenance quality, conflict, episode history.
    for required in (
        "RECENTLY_CREATED",
        "REUSE_RESISTANCE",
        "VERIFIED_RESISTANCE",
        "TRUST_EVIDENCE",
        "NEGATIVE_IMPORTANCE",
        "FAILURE_IMPORTANCE",
        "SUPERSEDED_HISTORY",
        "CONFLICT_VISIBLE",
        "ENVIRONMENT_MISMATCH",
        "PROVENANCE_ABSENT",
        "EPISODE_HISTORY",
        "STALE_UNVERIFIED",
        "STALE_DEGRADED",
        "ARCHIVAL_BY_AGE",
    ):
        assert required in reason_members
