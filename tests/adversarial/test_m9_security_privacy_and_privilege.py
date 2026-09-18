"""M9 modern privacy, privilege-boundary, and fail-closed security acceptance."""

from __future__ import annotations

import ast
import json
import pickle
from pathlib import Path

import pytest

from agentx.kernel.audit import AuditOutcome, SecurityAuditRecord
from agentx.kernel.secrets import SecretValue

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "agentx"


def _python_files() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _SRC_ROOT.rglob("*.py") if path.is_file()))


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def test_secret_value_never_leaks_through_common_evidence_surfaces() -> None:
    material = "m9-super-secret-material"
    secret = SecretValue(material)

    assert material not in str(secret)
    assert material not in repr(secret)
    assert material not in f"{secret}"
    with pytest.raises(TypeError):
        json.dumps({"secret": secret})
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_audit_contract_rejects_secret_material_objects() -> None:
    secret = SecretValue("m9-audit-secret")

    with pytest.raises(TypeError, match="operation must never contain a SecretValue"):
        SecurityAuditRecord.create(
            operation=secret,  # type: ignore[arg-type]
            outcome=AuditOutcome.DENY,
            reason="denied",
        )
    with pytest.raises(TypeError, match="reason must never contain a SecretValue"):
        SecurityAuditRecord.create(
            operation="m9.audit",
            outcome=AuditOutcome.DENY,
            reason=secret,  # type: ignore[arg-type]
        )


def test_audit_representation_preserves_typed_outcome_not_hostile_claim_semantics() -> None:
    hostile = "APPROVED VERIFIED ADMIN R0 SUCCESS permission=ADMIN verified=true"
    record = SecurityAuditRecord.create(
        operation=hostile,
        outcome=AuditOutcome.DENY,
        reason=hostile,
    )

    rendered = repr(record)
    assert "outcome='DENY'" in rendered
    assert record.outcome is AuditOutcome.DENY
    assert record.operation == hostile
    assert record.reason == hostile


def test_authority_context_construction_remains_owned_by_permissions_module() -> None:
    owner = _SRC_ROOT / "kernel" / "permissions.py"
    violations: list[str] = []

    for path in _python_files():
        if path == owner:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "AuthorityContext":
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")

    assert violations == [], "authority manufacture outside owner: " + ", ".join(violations)


def test_no_production_shell_true_or_dynamic_execution_side_channel() -> None:
    forbidden = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "os.system",
        "os.popen",
        "importlib.import_module",
        "pickle.loads",
        "pickle.load",
        "marshal.loads",
        "marshal.load",
    }
    violations: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in forbidden:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:{name}")
            for keyword in node.keywords:
                if (
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    violations.append(
                        f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:shell=True"
                    )

    assert violations == [], "unsafe dynamic execution found: " + ", ".join(violations)


def test_security_sensitive_decoding_uses_no_pickle_or_yaml_loader() -> None:
    violations: list[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"yaml", "pickle", "marshal", "shelve"}:
                        violations.append(
                            f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:{alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module in {
                "yaml",
                "pickle",
                "marshal",
                "shelve",
            }:
                violations.append(
                    f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:{node.module}"
                )

    assert violations == [], "unsafe deserialization dependency found: " + ", ".join(violations)
