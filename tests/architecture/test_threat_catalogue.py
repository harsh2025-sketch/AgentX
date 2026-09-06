"""Architecture checks for the C9.01 whole-system threat catalogue.

The catalogue is documentation under ``docs/security/``. It is not a runtime
security subsystem and must not live in ``src/agentx``. These tests keep the
JSON schema, stable IDs, referenced paths, required coverage, and markdown
companion honest so C9.02-C9.08 can cite ``AX-T-NNN`` without a parallel map.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_DIR = _REPO_ROOT / "docs" / "security"
_CATALOGUE_PATH = _SECURITY_DIR / "threat-catalogue.json"
_MODEL_PATH = _SECURITY_DIR / "threat-model.md"
_README_PATH = _SECURITY_DIR / "README.md"

_THREAT_ID = re.compile(r"^AX-T-\d{3}$")
_MARKDOWN_THREAT_ID = re.compile(r"AX-T-\d{3}")
_INVARIANT_ID = re.compile(r"^I[1-8]$")
_FUTURE_TEST = re.compile(r"^C9\.\d{2}\b")

_REQUIRED_CATALOGUE_KEYS = frozenset(
    {
        "schema_version",
        "catalogue_id",
        "title",
        "owner_task",
        "notes",
        "assets",
        "trust_boundaries",
        "attacker_classes",
        "invariants",
        "threats",
    }
)
_REQUIRED_THREAT_KEYS = frozenset(
    {
        "id",
        "title",
        "asset",
        "entry_point",
        "trust_boundary",
        "precondition",
        "attack",
        "impact",
        "existing_mitigation",
        "mitigation_refs",
        "required_regression_test",
        "residual_risk",
        "attacker_classes",
        "invariants",
        "status",
    }
)
_REQUIRED_THREAT_TEXT_KEYS = (
    "id",
    "title",
    "asset",
    "entry_point",
    "trust_boundary",
    "precondition",
    "attack",
    "impact",
    "existing_mitigation",
    "required_regression_test",
    "residual_risk",
    "status",
)
_ALLOWED_STATUS = frozenset({"mitigated", "partial", "residual"})
_C9_INVARIANTS = tuple(f"I{index}" for index in range(1, 9))
_MISSION_ASSETS = frozenset(
    {
        "user_data",
        "secrets",
        "machine_state",
        "external_accounts_effects",
        "hive_knowledge",
        "procedures",
        "verification_evidence",
        "permissions",
        "audit_history",
        "device_state",
        "generated_adaptive_code",
    }
)
_MISSION_BOUNDARIES = frozenset(
    {
        "clients_ui_to_kernel",
        "cognition_to_kernel",
        "kernel_to_capabilities",
        "capabilities_to_os_browser",
        "procedures_to_kernel",
        "hive_to_consumers",
        "learning_to_execution",
        "research_to_hive",
        "repair_to_procedures",
        "persistence_to_domain",
        "future_devices_self_extension",
    }
)
_MISSION_ATTACKERS = frozenset(
    {
        "malicious_external_content",
        "prompt_injection",
        "malicious_webpage_document",
        "poisoned_memory",
        "compromised_capability_provider",
        "malformed_local_state",
        "hostile_model_output",
        "untrusted_generated_capability",
        "cross_scope_data_attacker",
        "resource_exhaustion_attacker",
    }
)


def _as_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{name} must be a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _as_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{name} must be a JSON array, got {type(value).__name__}")
    return list(value)


def _as_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"{name} must be a non-empty string")
    return value


def _as_str_list(value: object, name: str) -> tuple[str, ...]:
    items = _as_list(value, name)
    strings: list[str] = []
    for index, item in enumerate(items):
        strings.append(_as_str(item, f"{name}[{index}]"))
    return tuple(strings)


def _load_catalogue() -> dict[str, object]:
    payload = json.loads(_CATALOGUE_PATH.read_text(encoding="utf-8"))
    return _as_mapping(payload, "catalogue")


def _named_records(catalogue: dict[str, object], field: str) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for index, item in enumerate(_as_list(catalogue[field], field)):
        records.append(_as_mapping(item, f"{field}[{index}]"))
    return tuple(records)


def _record_ids(records: tuple[dict[str, object], ...], field: str) -> tuple[str, ...]:
    return tuple(_as_str(record["id"], f"{field}.id") for record in records)


def _threats(catalogue: dict[str, object]) -> tuple[dict[str, object], ...]:
    return _named_records(catalogue, "threats")


@pytest.fixture(scope="module")
def catalogue() -> dict[str, object]:
    return _load_catalogue()


@pytest.fixture(scope="module")
def threats(catalogue: dict[str, object]) -> tuple[dict[str, object], ...]:
    return _threats(catalogue)


def test_security_docs_live_under_docs_security_not_src() -> None:
    assert _CATALOGUE_PATH.is_file()
    assert _MODEL_PATH.is_file()
    assert _README_PATH.is_file()
    src_security = _REPO_ROOT / "src" / "agentx" / "security"
    assert not src_security.exists()


def test_catalogue_schema_and_owner(catalogue: dict[str, object]) -> None:
    assert set(catalogue) == _REQUIRED_CATALOGUE_KEYS
    assert catalogue["schema_version"] == 1
    assert catalogue["catalogue_id"] == "agentx-c9.01-threat-catalogue"
    assert catalogue["owner_task"] == "C9.01"
    notes = _as_str(catalogue["notes"], "notes")
    assert "not a runtime security subsystem" in notes


def test_declared_coverage_matches_c9_01_mission(catalogue: dict[str, object]) -> None:
    assets = set(_record_ids(_named_records(catalogue, "assets"), "assets"))
    boundaries = set(_record_ids(_named_records(catalogue, "trust_boundaries"), "trust_boundaries"))
    attackers = set(_record_ids(_named_records(catalogue, "attacker_classes"), "attacker_classes"))
    invariants = _record_ids(_named_records(catalogue, "invariants"), "invariants")
    assert assets == _MISSION_ASSETS
    assert boundaries == _MISSION_BOUNDARIES
    assert attackers == _MISSION_ATTACKERS
    assert invariants == _C9_INVARIANTS
    for invariant in _named_records(catalogue, "invariants"):
        assert _INVARIANT_ID.fullmatch(_as_str(invariant["id"], "invariants.id"))
        assert _as_str(invariant["statement"], "invariants.statement")


def test_threat_ids_are_unique_and_stable(threats: tuple[dict[str, object], ...]) -> None:
    assert threats, "C9.01 catalogue must contain threats"
    ids = [_as_str(threat["id"], "threat.id") for threat in threats]
    assert all(_THREAT_ID.fullmatch(threat_id) for threat_id in ids)
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


@pytest.mark.parametrize(
    "field",
    sorted(_REQUIRED_THREAT_KEYS),
)
def test_every_threat_has_required_fields(
    threats: tuple[dict[str, object], ...],
    field: str,
) -> None:
    for threat in threats:
        assert field in threat, f"{threat.get('id')} missing {field}"


def test_threat_field_types_and_vocabularies(
    catalogue: dict[str, object],
    threats: tuple[dict[str, object], ...],
) -> None:
    assets = set(_record_ids(_named_records(catalogue, "assets"), "assets"))
    boundaries = set(_record_ids(_named_records(catalogue, "trust_boundaries"), "trust_boundaries"))
    attackers = set(_record_ids(_named_records(catalogue, "attacker_classes"), "attacker_classes"))
    invariants = set(_record_ids(_named_records(catalogue, "invariants"), "invariants"))
    for threat in threats:
        threat_id = _as_str(threat["id"], "id")
        assert set(threat) == _REQUIRED_THREAT_KEYS, threat_id
        for key in _REQUIRED_THREAT_TEXT_KEYS:
            _as_str(threat[key], f"{threat_id}.{key}")
        assert threat["asset"] in assets, threat_id
        assert threat["trust_boundary"] in boundaries, threat_id
        assert threat["status"] in _ALLOWED_STATUS, threat_id
        threat_attackers = _as_str_list(threat["attacker_classes"], f"{threat_id}.attackers")
        threat_invariants = _as_str_list(threat["invariants"], f"{threat_id}.invariants")
        assert threat_attackers, threat_id
        assert threat_invariants, threat_id
        assert set(threat_attackers) <= attackers, threat_id
        assert set(threat_invariants) <= invariants, threat_id
        refs = _as_str_list(threat["mitigation_refs"], f"{threat_id}.mitigation_refs")
        assert refs, threat_id


def test_mitigation_and_regression_paths_exist(
    threats: tuple[dict[str, object], ...],
) -> None:
    for threat in threats:
        threat_id = _as_str(threat["id"], "id")
        for ref in _as_str_list(threat["mitigation_refs"], f"{threat_id}.mitigation_refs"):
            path = _REPO_ROOT / Path(ref)
            assert path.is_file(), f"{threat_id} mitigation_ref missing: {ref}"
        test_ref = _as_str(threat["required_regression_test"], f"{threat_id}.test")
        if _FUTURE_TEST.match(test_ref):
            continue
        file_part = test_ref.split("::", 1)[0]
        path = _REPO_ROOT / Path(file_part)
        assert path.is_file(), f"{threat_id} required_regression_test missing: {file_part}"


def test_threats_cover_mission_assets_boundaries_attackers_invariants(
    threats: tuple[dict[str, object], ...],
) -> None:
    used_assets = {cast(str, threat["asset"]) for threat in threats}
    used_boundaries = {cast(str, threat["trust_boundary"]) for threat in threats}
    used_attackers: set[str] = set()
    used_invariants: set[str] = set()
    for threat in threats:
        used_attackers.update(_as_str_list(threat["attacker_classes"], "attackers"))
        used_invariants.update(_as_str_list(threat["invariants"], "invariants"))
    assert used_assets == _MISSION_ASSETS
    assert used_boundaries == _MISSION_BOUNDARIES
    assert used_attackers == _MISSION_ATTACKERS
    assert used_invariants == set(_C9_INVARIANTS)


def test_markdown_model_agrees_with_catalogue(
    threats: tuple[dict[str, object], ...],
) -> None:
    markdown = _MODEL_PATH.read_text(encoding="utf-8")
    readme = _README_PATH.read_text(encoding="utf-8")
    catalogue_ids = [_as_str(threat["id"], "id") for threat in threats]
    markdown_ids = _MARKDOWN_THREAT_ID.findall(markdown)
    assert set(catalogue_ids) <= set(markdown_ids)
    extra = set(markdown_ids) - set(catalogue_ids)
    assert extra == set(), f"markdown cites unknown threat IDs: {sorted(extra)}"
    for threat in threats:
        threat_id = _as_str(threat["id"], "id")
        heading = f"### {threat_id}: {_as_str(threat['title'], 'title')}"
        assert heading in markdown, threat_id
    for label in (
        "**Asset:**",
        "**Entry point:**",
        "**Trust boundary:**",
        "**Precondition:**",
        "**Attack:**",
        "**Impact:**",
        "**Existing mitigation:**",
        "**Required regression / test:**",
        "**Residual risk:**",
    ):
        assert label in markdown
    assert "C9.01" in markdown
    assert "threat-catalogue.json" in markdown
    assert "A1.10" in markdown
    assert "docs/security" in readme
    assert "AX-T-NNN" in readme


def test_catalogue_is_not_imported_by_runtime_package() -> None:
    src_root = _REPO_ROOT / "src" / "agentx"
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "threat-catalogue" in text or "docs/security" in text:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == []
