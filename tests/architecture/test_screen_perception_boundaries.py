"""Architecture guards for the C5.06 screen/perception representation boundary.

Static checks that pin the C5.06 invariants:

* the representation lives in ``agentx.capabilities`` as one flat module;
* it imports nothing beyond the standard library and the canonical
  ``agentx.core.ids`` artifact identity (provider-neutral: no browser, no
  Windows, no vision/OCR/model stack, no persistence, no authority code);
* it defines data contracts only: no Protocol, no reader/request/provider
  port, no capture/action/grounding/fusion surface;
* its vocabulary, geometry fields, enum members, and canonical tuples stay
  exactly the assigned closed set;
* there is no random identity generation, no dynamic code, no I/O, and no
  non-deterministic serialization primitive.

These are architecture guardrails, not security enforcement; authority
remains owned by the Trusted Kernel.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.capabilities.screen_perception import (
    CANONICAL_PERCEPTION_COORDINATE_SPACES,
    CANONICAL_PERCEPTION_OBSERVATION_STATES,
    PerceptionBoundingBox,
    PerceptionCandidate,
    PerceptionCandidateId,
    PerceptionCanvas,
    PerceptionCoordinateSpace,
    PerceptionObservation,
    PerceptionObservationId,
    PerceptionObservationState,
    PerceptionProviderId,
    PerceptionSourceId,
)

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "capabilities" / "screen_perception.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _imported_modules() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _public_classes() -> set[str]:
    return {
        node.name
        for node in _TREE.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }


def test_perception_representation_lives_in_capabilities_boundary() -> None:
    assert _MODULE.exists()
    assert _MODULE.parts[-3:] == ("agentx", "capabilities", "screen_perception.py")


def test_module_imports_only_stdlib_and_canonical_artifact_identity() -> None:
    imported = _imported_modules()
    allowed_stdlib = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "json",
        "math",
        "re",
        "typing",
    }
    assert imported <= allowed_stdlib | {"agentx.core.ids"}
    assert imported.isdisjoint(
        {
            "agentx.capabilities.browser_provider",
            "agentx.capabilities.browser_connection",
            "agentx.capabilities.browser_dom",
            "agentx.capabilities.browser_selection",
            "agentx.capabilities.windows",
            "agentx.core.artifacts",
            "agentx.core.provenance",
        }
    )
    agentx_imports = {name for name in imported if name.startswith("agentx")}
    assert agentx_imports == {"agentx.core.ids"}


def test_module_imports_no_vision_ocr_image_or_model_stack() -> None:
    imported = _imported_modules()
    forbidden = {
        "cv2",
        "PIL",
        "pytesseract",
        "easyocr",
        "torch",
        "torchvision",
        "transformers",
        "sentence_transformers",
        "openai",
        "onnxruntime",
        "numpy",
        "skimage",
        "tesseract",
    }
    assert imported.isdisjoint(forbidden)
    assert imported.isdisjoint(
        {"playwright", "selenium", "pywinauto", "pywin32", "win32gui", "ctypes"}
    )


def test_module_imports_no_transport_process_persistence_or_authority_surface() -> None:
    imported = _imported_modules()
    forbidden = {
        "socket",
        "websockets",
        "requests",
        "urllib",
        "http",
        "subprocess",
        "asyncio",
        "sqlite3",
        "random",
        "uuid",
        "os",
        "sys",
        "pathlib",
        "importlib",
    }
    assert imported.isdisjoint(forbidden)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.capabilities",
    )
    assert all(
        not (name.startswith(prefix) and name != "agentx.core.ids")
        for name in imported
        for prefix in forbidden_prefixes
        if name.startswith("agentx")
    )


def test_module_calls_no_dynamic_code_io_or_execution_primitive() -> None:
    calls = _called_names()
    forbidden = {
        "eval",
        "exec",
        "__import__",
        "open",
        "urlopen",
        "connect",
        "send",
        "recv",
        "Popen",
        "run",
        "system",
        "spawn",
        "load",
        "pickle",
        "yaml",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
    # ``re.compile`` (a module-level constant pattern) and ``json.dumps``
    # (deterministic serialization) are the only allowed "dynamic-looking"
    # primitives, and both are compile-time/constant or pure-string calls.
    assert calls.isdisjoint(forbidden)
    assert "eval(" not in _SOURCE
    assert "exec(" not in _SOURCE


def test_module_uses_no_random_or_uuid_identity_generation() -> None:
    assert "random" not in _imported_modules()
    assert "uuid" not in _imported_modules()
    assert "uuid4" not in _called_names()
    assert "token_hex" not in _called_names()
    assert "urandom" not in _called_names()


def test_representation_defines_no_provider_port_or_protocol() -> None:
    assert "Protocol" not in _imported_modules()
    lowered = _SOURCE.lower()
    for token in ("class perceptionreader", "class perceptionrequest", "def observe"):
        assert token not in lowered


def test_module_has_no_capture_ocr_grounding_fusion_or_action_function() -> None:
    function_names = _function_names()
    forbidden = {
        "capture",
        "observe",
        "read",
        "request",
        "click",
        "fill",
        "type",
        "submit",
        "navigate",
        "execute",
        "run",
        "ground",
        "fuse",
        "link",
        "ocr",
        "recognize",
        "detect",
        "classify",
        "verify",
        "authorize",
        "activate",
    }
    assert function_names.isdisjoint(forbidden)


def test_public_class_vocabulary_is_exactly_the_canonical_data_model() -> None:
    assert _public_classes() == {
        "PerceptionBoundingBox",
        "PerceptionCandidate",
        "PerceptionCandidateId",
        "PerceptionCanvas",
        "PerceptionCoordinateSpace",
        "PerceptionObservation",
        "PerceptionObservationId",
        "PerceptionObservationState",
        "PerceptionProviderId",
        "PerceptionSourceId",
        "PerceptionValidationError",
        "UnsupportedPerceptionSchemaVersionError",
    }
    for class_name in _public_classes():
        assert "Reader" not in class_name
        assert "Provider" not in class_name or class_name == "PerceptionProviderId"
        assert "Capability" not in class_name
        assert "Registry" not in class_name
        assert "Store" not in class_name
        assert "Port" not in class_name
        assert "Session" not in class_name


def test_geometry_and_value_fields_are_the_exact_assigned_set() -> None:
    assert [field.name for field in fields(PerceptionCanvas)] == ["width", "height"]
    assert [field.name for field in fields(PerceptionBoundingBox)] == [
        "x",
        "y",
        "width",
        "height",
    ]
    assert [field.name for field in fields(PerceptionCandidate)] == [
        "candidate_id",
        "region",
        "label",
        "text",
        "confidence",
        "parent_id",
    ]
    assert [field.name for field in fields(PerceptionObservation)] == [
        "observation_id",
        "state",
        "observed_at",
        "coordinate_space",
        "canvas",
        "candidates",
        "capture_artifact_id",
        "state_detail",
        "schema_version",
    ]


def test_candidate_fields_claim_no_liveness_safety_or_authority() -> None:
    names = {field.name for field in fields(PerceptionCandidate)}
    observation_names = {field.name for field in fields(PerceptionObservation)}
    forbidden = {
        "live",
        "liveness",
        "interactable",
        "clickable",
        "enabled",
        "focusable",
        "safe",
        "verified",
        "verification",
        "authorized",
        "permission",
        "role",
        "handle",
        "native_ref",
        "dom_ref",
        "uia_ref",
        "window_handle",
    }
    assert names.isdisjoint(forbidden)
    assert observation_names.isdisjoint(forbidden)


def test_identity_chain_vocabulary_is_exactly_provider_source_observation_candidate() -> None:
    assert [field.name for field in fields(PerceptionProviderId)] == ["value"]
    assert [field.name for field in fields(PerceptionSourceId)] == ["provider_id", "value"]
    assert [field.name for field in fields(PerceptionObservationId)] == ["source_id", "value"]
    assert [field.name for field in fields(PerceptionCandidateId)] == [
        "observation_id",
        "value",
    ]


def test_state_and_coordinate_vocabularies_are_exactly_the_canonical_set() -> None:
    assert [state.value for state in PerceptionObservationState] == [
        "observed",
        "stale",
        "unavailable",
    ]
    assert [space.value for space in PerceptionCoordinateSpace] == [
        "unknown",
        "unsupported",
        "image_pixels_top_left",
    ]
    assert CANONICAL_PERCEPTION_OBSERVATION_STATES == (
        PerceptionObservationState.OBSERVED,
        PerceptionObservationState.STALE,
        PerceptionObservationState.UNAVAILABLE,
    )
    assert CANONICAL_PERCEPTION_COORDINATE_SPACES == (
        PerceptionCoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
    )
    assert "definitely_available" not in PerceptionObservationState.__members__
    assert "normalized_01" not in PerceptionCoordinateSpace.__members__
    assert "css_pixels" not in PerceptionCoordinateSpace.__members__


def test_public_methods_are_representation_and_serialization_only() -> None:
    public_methods = {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    # Data-model validation plus dict/JSON serialization and strict parsing;
    # the only public properties are the provider/source provenance passthroughs.
    assert public_methods <= {
        "from_dict",
        "from_json",
        "to_dict",
        "to_json",
        "provider_id",
        "source_id",
    }


def test_module_source_has_no_authority_or_persistence_symbols() -> None:
    forbidden_symbols = {
        "ActionGate",
        "AuthorityContext",
        "EmergencyStop",
        "Permission",
        "ResourceBudget",
        "RiskLevel",
        "Task",
        "Procedure",
        "CapabilityRegistry",
        "ArtifactRecord",
        "EvidenceReference",
        "create table",
        "insert into",
        "migration",
    }
    for symbol in forbidden_symbols:
        assert symbol not in _SOURCE, f"{symbol!r} must not appear in the C5.06 module"


def test_serialization_uses_only_deterministic_json_primitives() -> None:
    lowered = _SOURCE.lower()
    assert '"sort_keys": true' in lowered or "sort_keys=true" in lowered
    assert '"allow_nan": false' in lowered or "allow_nan=false" in lowered
    assert "ensure_ascii" in lowered
    assert "math.isfinite" in _SOURCE
    assert "hash(" not in _SOURCE


def test_documentation_declares_coordinate_convention_and_untrusted_data() -> None:
    lowered = " ".join(_SOURCE.lower().split())
    assert "top-left" in lowered
    assert "increases downward" in lowered
    assert "untrusted" in lowered
    assert "not automatically" in lowered
    assert "fails closed" in lowered
