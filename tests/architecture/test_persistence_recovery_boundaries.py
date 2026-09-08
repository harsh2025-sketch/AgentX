"""Architecture guards for the M6.03 durable-state recovery assessor.

The recovery module is a conservative, read-only assessment boundary inside
``agentx.infrastructure``. These machine-testable guards enforce that it:

- imports only canonical infrastructure store/database contracts plus
  ``agentx.core.ids`` (no kernel, capabilities, cognition, learning, hive,
  procedures, or model/research code);
- executes no destructive SQL vocabulary (DELETE/DROP/ALTER/TRUNCATE/
  REPLACE/UPDATE/VACUUM/INSERT/CREATE and friends never appear in SQL it
  executes) and never performs dynamic execution;
- keeps a closed disposition/kind/recommendation vocabulary;
- keeps its migration-plan mirror synchronized with the canonical
  ``agentx.infrastructure.persistence._MIGRATIONS`` plan;
- is side-effect free at import time (no database file created).

The destructive-vocabulary guard inspects executable SQL strings — the first
argument of ``execute``-style calls — rather than prose, so documentation
strings do not cause false positives.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import agentx.infrastructure.recovery as recovery
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECOVERY_MODULE = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "recovery.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.core.ids",
        "agentx.infrastructure.artifact_store",
        "agentx.infrastructure.audit_store",
        "agentx.infrastructure.episode_store",
        "agentx.infrastructure.event_journal",
        "agentx.infrastructure.knowledge_store",
        "agentx.infrastructure.negative_experience_store",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.procedure_store",
    }
)

_FORBIDDEN_SUBSYSTEMS = (
    "agentx.kernel",
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.learning",
    "agentx.hive",
    "agentx.procedures",
    "agentx.core.artifacts",
    "agentx.core.audit_records",
    "agentx.core.episodes",
    "agentx.core.events",
    "agentx.core.knowledge",
    "agentx.core.negative_experience",
    "agentx.core.procedures",
)

# Word-bounded destructive SQL vocabulary (checked against executed SQL only).
_DESTRUCTIVE_SQL = re.compile(
    r"\b(?:DELETE|DROP|ALTER|TRUNCATE|REPLACE|UPDATE|VACUUM|INSERT|CREATE|"
    r"BEGIN|COMMIT|ROLLBACK|REINDEX|ATTACH|DETACH)\b"
)


def _tree() -> ast.Module:
    return ast.parse(_RECOVERY_MODULE.read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            result.add(node.module)
    return result


def test_recovery_imports_only_canonical_infrastructure_contracts() -> None:
    imports = _imports(_tree())
    agentx_imports = {name for name in imports if name.startswith("agentx")}
    assert agentx_imports <= _ALLOWED_AGENTX_IMPORTS
    forbidden_hits = sorted(
        subsystem
        for subsystem in _FORBIDDEN_SUBSYSTEMS
        if any(
            module == subsystem or module.startswith(f"{subsystem}.") for module in agentx_imports
        )
    )
    assert not forbidden_hits, f"forbidden imports: {forbidden_hits}"


def test_recovery_never_executes_destructive_sql() -> None:
    tree = _tree()
    executed_statements: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"execute", "executemany", "executescript"}:
            continue
        assert not node.args or isinstance(node.args[0], (ast.Constant, ast.JoinedStr)), (
            "recovery.py must only execute static SQL statements"
        )
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            executed_statements.append(node.args[0].value)
    assert executed_statements, "expected recovery.py to execute read-only SQL"
    for statement in executed_statements:
        normalized = statement.strip().upper()
        assert normalized.startswith(("SELECT", "PRAGMA")), f"unexpected SQL: {statement!r}"
        assert not _DESTRUCTIVE_SQL.search(statement), f"destructive SQL: {statement!r}"


def test_recovery_has_no_commit_rollback_or_dynamic_execution_calls() -> None:
    tree = _tree()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    for forbidden in ("exec", "eval", "compile", "executescript", "commit", "rollback"):
        assert forbidden not in called, f"recovery.py must not call {forbidden!r}"


def test_recovery_vocabularies_are_closed() -> None:
    assert {member.value for member in recovery.RecoveryDisposition} == {
        "HEALTHY",
        "DEGRADED_READ_ONLY",
        "BLOCK_STARTUP",
        "INSUFFICIENT_EVIDENCE",
    }
    assert {member.value for member in recovery.StartupRecommendation} == {
        "CONTINUE",
        "CONTINUE_DEGRADED",
        "STOP_AND_ESCALATE",
    }
    assert {member.value for member in recovery.RecoveryCheckKind} == {
        "database.file_missing",
        "database.unreadable",
        "migration.metadata_unreadable",
        "migration.history_invalid",
        "migration.schema_newer_than_supported",
        "structure.expected_table_missing",
        "store.read_failed",
        "store.corrupt_record",
        "inspection.fault",
    }


def test_migration_registry_mirrors_canonical_migration_plan() -> None:
    """The read-only schema mirror must stay in sync with the canonical plan.

    ``_MIGRATION_REGISTRY`` is deliberately private, mirror metadata inside
    the recovery module. When the canonical persistence plan gains a
    migration, this guard forces the mirror (and its docs) to be updated.
    """
    canonical_names = tuple(migration.name for migration in _MIGRATIONS)
    mirror_names = tuple(entry.name for entry in recovery._MIGRATION_REGISTRY)
    assert mirror_names == canonical_names

    canonical_tables_by_version: dict[int, set[str]] = {}
    table_pattern = re.compile(r"CREATE TABLE\s+(?P<table>\w+)")
    for migration in _MIGRATIONS:
        tables: set[str] = set()
        for statement in migration.statements:
            tables.update(table_pattern.findall(statement))
        canonical_tables_by_version[migration.version] = tables

    for entry in recovery._MIGRATION_REGISTRY:
        expected = canonical_tables_by_version[entry.version]
        assert set(entry.tables) == expected, (
            f"migration mirror tables for version {entry.version} drifted from canonical plan"
        )
    assert len(_MIGRATIONS) == recovery._SUPPORTED_SCHEMA_VERSION


def test_importing_recovery_creates_no_files(tmp_path: Path) -> None:
    """Importing the recovery module must not touch the filesystem."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agentx.infrastructure.recovery",
        ],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(_REPO_ROOT / "src"),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
