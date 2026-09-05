"""Architecture guards for C2.04 artifact/audit store placement and boundaries.

Mirrors the C2.01/C2.02/C2.03 placement guards. The decisive C2.04-specific
invariant: ``agentx.infrastructure.audit_store`` persists the canonical C1.09
kernel contract WITHOUT importing ``agentx.kernel`` at all — the boundary
manifest forbids that edge, so the store consumes the kernel record through a
structural protocol seam and stores every controlled field as inert text. The
kernel module itself stays untouched, the audit vocabulary stays
single-sourced in ``agentx.kernel.audit``, and the audit store stays distinct
from the EventJournal.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx.core.artifacts import ArtifactDigestAlgorithm, ArtifactKind
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_ARTIFACT_CONTRACT = _AGENTX_SRC / "core" / "artifacts.py"
_ARTIFACT_STORE = _AGENTX_SRC / "infrastructure" / "artifact_store.py"
_AUDIT_STORE = _AGENTX_SRC / "infrastructure" / "audit_store.py"
_KERNEL_AUDIT = _AGENTX_SRC / "kernel" / "audit.py"


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _top_level_class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _agentx_imports(path: Path) -> set[str]:
    return {module for module in _imported_modules(path) if module.startswith("agentx")}


# ---------------------------------------------------------------------------
# Single canonical definitions
# ---------------------------------------------------------------------------


def test_artifact_record_contract_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.core.artifacts`` defines the artifact-record contract."""
    for name in (
        "ArtifactRecord",
        "ArtifactKind",
        "ArtifactDigest",
        "ArtifactDigestAlgorithm",
    ):
        assert _class_definitions(name) == [Path("agentx/core/artifacts.py")], name


def test_artifact_id_reuses_the_existing_canonical_id_type() -> None:
    """C2.04 reuses ``ArtifactId`` from the canonical ID system and does not
    introduce a competing artifact identifier type."""
    assert _class_definitions("ArtifactId") == [Path("agentx/core/ids.py")]


def test_artifact_store_and_audit_store_have_exactly_one_canonical_definition() -> None:
    assert _class_definitions("ArtifactStore") == [Path("agentx/infrastructure/artifact_store.py")]
    assert _class_definitions("AuditStore") == [Path("agentx/infrastructure/audit_store.py")]


def test_c1_09_audit_contract_remains_untouched_and_single_sourced() -> None:
    """C2.04 persists the canonical C1.09 audit types; it does not redefine
    them anywhere."""
    for name in ("SecurityAuditRecord", "AuditContext", "AuditOutcome"):
        assert _class_definitions(name) == [Path("agentx/kernel/audit.py")], name

    # The kernel security modules were not modified to know about storage.
    kernel_imports = _imported_modules(_KERNEL_AUDIT)
    assert not any("infrastructure" in module for module in kernel_imports)
    assert "sqlite3" not in kernel_imports


# ---------------------------------------------------------------------------
# Import placement
# ---------------------------------------------------------------------------


def test_artifact_contract_module_imports_no_outer_subsystem() -> None:
    """``agentx.core`` stays inward: only stdlib and canonical IDs."""
    imported = _imported_modules(_ARTIFACT_CONTRACT)
    outer = [
        module
        for module in imported
        if module.startswith("agentx.") and "agentx.core" not in module
    ]
    assert outer == []
    assert "sqlite3" not in imported  # the contract is storage-independent


def test_artifact_store_depends_only_on_core_and_persistence() -> None:
    agentx_imports = _agentx_imports(_ARTIFACT_STORE)
    assert agentx_imports <= {
        "agentx.core.ids",
        "agentx.core.artifacts",
        "agentx.infrastructure.persistence",
    }


def test_audit_store_depends_only_on_core_ids_and_persistence() -> None:
    agentx_imports = _agentx_imports(_AUDIT_STORE)
    assert agentx_imports <= {
        "agentx.core.ids",
        "agentx.infrastructure.persistence",
    }


def test_audit_store_never_imports_the_kernel() -> None:
    """The manifest forbids infrastructure -> kernel; C2.04 respects it."""
    for path in (_AUDIT_STORE, _ARTIFACT_STORE):
        violations = [
            module for module in _imported_modules(path) if module.startswith("agentx.kernel")
        ]
        assert violations == [], path

    # And it never names the kernel's audit symbols as imports either.
    imported_names = _imported_names(_AUDIT_STORE)
    assert imported_names.isdisjoint({"SecurityAuditRecord", "AuditOutcome", "AuditContext"})


def test_new_store_modules_never_touch_transport_or_sibling_stores() -> None:
    forbidden_modules = {
        "agentx.infrastructure.event_bus",
        "agentx.infrastructure.event_journal",
    }
    for path in (_ARTIFACT_STORE, _AUDIT_STORE):
        imported = set(_imported_modules(path))
        assert imported.isdisjoint(forbidden_modules), path
        assert not any(
            marker in module
            for module in imported
            for marker in ("episode_store", "knowledge_store", "procedure_store", "hive")
        ), path


def test_kernel_stays_unaware_of_the_new_stores() -> None:
    for path in sorted((_AGENTX_SRC / "kernel").rglob("*.py")):
        assert not any(
            marker in module
            for module in _imported_modules(path)
            for marker in ("artifact", "audit_store")
        ), path


# ---------------------------------------------------------------------------
# No competing concepts, no blob platform, no higher-level behavior
# ---------------------------------------------------------------------------


def test_store_modules_define_no_competing_or_forbidden_concepts() -> None:
    reserved_class_names = {
        # Blob/storage platforms and lifecycle machinery are out of C2.04.
        "ArtifactManager",
        "FilesystemArtifactManager",
        "BlobStore",
        "ObjectStorage",
        "S3Client",
        "CloudStorage",
        "DownloadManager",
        "UploadManager",
        "ContentParser",
        "Compressor",
        "Deduplicator",
        "VectorIndex",
        "EmbeddingIndex",
        "SemanticMemory",
        "NegativeMemory",
        "ProvenanceLedger",
        "HiveCache",
        "CausalModel",
        # Kernel authority types must not be shadowed in infrastructure.
        "Permission",
        "AuthorityContext",
        "ActionGate",
        "RiskLevel",
        "ResourceEnvelope",
        "SecurityAuditRecord",
        "AuditRecord",
        "AuditEntryView",
    }
    for path in (_ARTIFACT_CONTRACT, _ARTIFACT_STORE, _AUDIT_STORE):
        assert _top_level_class_names(path).isdisjoint(reserved_class_names), path


def test_audit_store_does_not_duplicate_kernel_vocabulary_as_constants() -> None:
    """The store never re-derives the kernel's controlled vocabularies: no
    audit or risk vocabulary appears as string constants in the module."""
    tree = ast.parse(_AUDIT_STORE.read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    kernel_vocabulary = {
        "ALLOW",
        "DENY",
        "REQUIRE_CONFIRMATION",
        "SUCCEEDED",
        "FAILED",
        "STOP_REQUESTED",
        "R0",
        "R1",
        "R2",
        "R3",
        "R4",
        "READ",
        "WRITE",
        "EXECUTE",
        "EXTERNAL_EFFECT",
        "DESTRUCTIVE",
    }
    assert literals.isdisjoint(kernel_vocabulary)

    # And no Enum is defined there — enum awareness is purely structural.
    enum_bases = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = getattr(base, "id", getattr(base, "attr", ""))
                enum_bases += int(name in {"Enum", "StrEnum", "IntEnum", "EnumMeta"})
    assert enum_bases == 0


def test_artifact_store_exposes_only_inert_data_operations() -> None:
    """The ArtifactStore surface is insert/get/list — nothing that could act."""
    tree = ast.parse(_ARTIFACT_STORE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ArtifactStore":
            methods = {
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")
            }
            assert methods == {"insert", "get", "list_records"}

    tree = ast.parse(_AUDIT_STORE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AuditStore":
            methods = {
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")
            }
            assert methods == {"append", "get", "read"}


def test_hive_and_procedures_packages_remain_unimplemented() -> None:
    """C2.04 is storage foundation only: no higher-level Hive or procedure
    package code."""
    for package in ("hive", "procedures"):
        files = sorted(
            path.name for path in (_AGENTX_SRC / package).rglob("*.py") if path.is_file()
        )
        assert files == ["__init__.py"], package


def test_no_new_package_or_module_outside_the_canonical_lanes() -> None:
    """C2.04 adds exactly one core contract module and two infrastructure
    store modules; nothing lands in kernel or elsewhere."""
    new_expected = {
        "agentx/core/artifacts.py",
        "agentx/infrastructure/artifact_store.py",
        "agentx/infrastructure/audit_store.py",
    }
    existing = {
        str(path.relative_to(_SRC_ROOT)) for path in _AGENTX_SRC.rglob("*.py") if path.is_file()
    }
    assert new_expected <= existing
    assert not {
        str(path.relative_to(_SRC_ROOT))
        for path in (_AGENTX_SRC / "kernel").rglob("*.py")
        if "artifact" in path.name or "audit_store" in path.name
    }


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def test_landed_migrations_remain_immutable() -> None:
    """Migrations v1-v5 keep their canonical versions and names forever."""
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)

    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))  # consecutive from 1
    assert names[:5] == (
        "create_persistence_metadata",
        "create_event_journal",
        "create_knowledge_store",
        "create_episode_store",
        "create_procedure_store",
    )


def test_c2_04_migrations_are_registered_by_name_and_adjacent() -> None:
    """C2.04 appends well-scoped migrations identified by name. If a
    concurrent worker lands between v5 and C2.04 at merge time, these
    migrations mechanically take the next free numbers; adjacency and
    name-uniqueness are the durable invariants, not the literal numbers."""
    by_name = {migration.name: migration for migration in _MIGRATIONS}
    assert "create_artifact_store" in by_name
    assert "create_audit_store" in by_name
    artifact = by_name["create_artifact_store"]
    audit = by_name["create_audit_store"]
    assert audit.version == artifact.version + 1
    assert any("agentx_artifacts" in statement for statement in artifact.statements)
    assert any("agentx_audit_log" in statement for statement in audit.statements)


def test_c2_04_migrations_do_not_touch_landed_schema() -> None:
    """Append-only: the new migrations only CREATE their own tables/indexes;
    they never ALTER or DROP anything pre-existing."""
    for migration in _MIGRATIONS:
        if migration.name not in {"create_artifact_store", "create_audit_store"}:
            continue
        for statement in migration.statements:
            normalized = " ".join(statement.split()).upper()
            assert normalized.startswith("CREATE TABLE") or normalized.startswith("CREATE INDEX"), (
                migration.name
            )
            assert "DROP" not in normalized
            assert "ALTER" not in normalized


# ---------------------------------------------------------------------------
# Zero runtime dependencies
# ---------------------------------------------------------------------------


_STDLIB_ALLOWED = {
    "__future__",
    "json",
    "sqlite3",
    "dataclasses",
    "datetime",
    "enum",
    "re",
    "typing",
    "uuid",
    "collections.abc",
    "contextlib",
}


def _non_agentx_imports(path: Path) -> set[str]:
    return {module for module in _imported_modules(path) if not module.startswith("agentx")}


def test_new_modules_introduce_zero_runtime_dependencies() -> None:
    for path in (_ARTIFACT_CONTRACT, _ARTIFACT_STORE, _AUDIT_STORE):
        external = _non_agentx_imports(path)
        assert external <= _STDLIB_ALLOWED, (path, sorted(external - _STDLIB_ALLOWED))


def test_project_runtime_dependencies_remain_empty() -> None:
    pyproject = _REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    assert data["project"]["dependencies"] == []


def test_controlled_vocabularies_carry_no_reserved_meaning() -> None:
    """The artifact kind/digest vocabularies stay storage-descriptive and
    never collide with reserved kernel/IR semantics."""
    kind_values = {member.value for member in ArtifactKind}
    algorithm_values = {member.value for member in ArtifactDigestAlgorithm}
    reserved = {"action", "verify", "branch", "reason", "node", "edge", "admin", "grant"}
    assert kind_values.isdisjoint(reserved)
    assert algorithm_values.isdisjoint(reserved)
