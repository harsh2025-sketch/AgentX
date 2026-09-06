"""Architecture guardrails for the A7.01 audio abstraction.

A7.01 is a *provider-neutral* boundary. These tests enforce the claims a reviewer
should not have to verify by eye: where the contract lives, that it stays a
dependency leaf, that no concrete speech vendor or platform audio API is reachable
from it, that it performs no I/O, and that no streaming STT, TTS, wake-word,
speaker-identification, voice-command, or model-call surface was quietly added on
the way.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agentx import _architecture
from agentx.core import audio

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "agentx"
_MODULE = _SRC / "core" / "audio.py"
_IDS = _SRC / "core" / "ids.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)

_VENDOR_ROOTS = (
    "azure",
    "deepgram",
    "elevenlabs",
    "faster_whisper",
    "google",
    "kokoro",
    "openai",
    "sherpa_onnx",
    "vosk",
    "whisper",
)
_PLATFORM_AUDIO_ROOTS = (
    "ctypes",
    "platform",
    "pyaudio",
    "sndfile",
    "soundcard",
    "sounddevice",
    "winsound",
)
_CODEC_ROOTS = ("aifc", "audioop", "sunau", "wave")
_RUNTIME_ROOTS = (
    "aiohttp",
    "asyncio",
    "http",
    "httpx",
    "multiprocessing",
    "os",
    "queue",
    "requests",
    "select",
    "socket",
    "sqlite3",
    "ssl",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
    "time",
    "urllib",
    "websocket",
    "websockets",
)


def _imports() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _import_roots() -> set[str]:
    return {name.split(".", maxsplit=1)[0] for name in _imports()}


def _agentx_imports() -> set[str]:
    return {name for name in _imports() if name.startswith("agentx.")}


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


def _module_level_names(tree: ast.Module) -> set[str]:
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign):
            defined.add(ast.unparse(node.target))
        elif isinstance(node, ast.Assign):
            defined.update(ast.unparse(target) for target in node.targets)
    return defined


def _subsystem_of(module: str) -> str | None:
    for package in _architecture.SUBSYSTEMS:
        if module == package or module.startswith(f"{package}."):
            return package
    return None


def test_contract_lives_in_core_as_a_shared_domain_boundary() -> None:
    assert audio.__name__ == "agentx.core.audio"
    assert _MODULE.as_posix().endswith("src/agentx/core/audio.py")
    # A package would invite "audio subsystem" drift; A7.01 is one contract module.
    assert not (_SRC / "core" / "audio").exists()


def test_audio_imports_only_canonical_core_contracts() -> None:
    assert _agentx_imports() == {
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.ids",
        "agentx.core.result",
    }


def test_core_audio_stays_a_dependency_leaf() -> None:
    """A7.01 adds no subsystem edge and no outward dependency from core."""

    for imported in _agentx_imports():
        assert _subsystem_of(imported) == _architecture.CORE, imported
    outward = (
        _architecture.KERNEL,
        _architecture.CAPABILITIES,
        _architecture.HIVE,
        _architecture.PROCEDURES,
        _architecture.COGNITION,
        _architecture.LEARNING,
        _architecture.INFRASTRUCTURE,
    )
    assert not [
        imported
        for imported in _imports()
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in outward)
    ]


def test_only_standard_library_is_reachable() -> None:
    assert _import_roots() <= {
        "__future__",
        "agentx",
        "base64",
        "binascii",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "math",
        "types",
        "typing",
    }


@pytest.mark.parametrize(
    "forbidden_group",
    [_VENDOR_ROOTS, _PLATFORM_AUDIO_ROOTS, _CODEC_ROOTS, _RUNTIME_ROOTS],
    ids=["speech-vendors", "platform-audio", "codecs", "runtime-and-io"],
)
def test_no_vendor_device_codec_or_runtime_dependency(forbidden_group: tuple[str, ...]) -> None:
    roots = _import_roots()
    assert roots.isdisjoint(forbidden_group)
    assert not (set(dir(audio)) & set(forbidden_group))


def test_no_vendor_is_even_named_in_the_contract_surface() -> None:
    """Vendor names may appear in prose comments, never as identifiers."""

    identifiers = {name.lower() for name in _module_level_names(_TREE)}
    names_in_symbols = {name.lower() for name in audio.__all__}
    for vendor in _VENDOR_ROOTS:
        assert vendor not in identifiers
        assert vendor not in names_in_symbols


def test_docs_page_records_the_boundary_and_its_non_goals() -> None:
    """A7.01 is a boundary others will build on, so its rules are written down."""

    page = _ROOT / "docs" / "audio_abstraction.md"
    assert page.is_file()
    text = page.read_text(encoding="utf-8")
    for marker in (
        "A7.01",
        "A7.02",
        "data, never authority",
        "AudioFrame",
        "AudioAdmissionDecision",
        "conformance",
        "no vendor selection",
        "Explicit non-goals",
    ):
        assert marker in text, marker


def test_public_surface_is_declared_exactly_once() -> None:
    public = {name for name in _module_level_names(_TREE) if not name.startswith("_")}
    assert public == set(audio.__all__), sorted(public ^ set(audio.__all__))
    for name in audio.__all__:
        assert hasattr(audio, name), name


def test_module_exposes_no_speech_or_command_runtime_vocabulary() -> None:
    forbidden_substrings = (
        "command",
        "conversation",
        "dialog",
        "embedding",
        "intent",
        "llm",
        "model",
        "prompt",
        "speaker",
        "session",
        "speech_recognition",
        "stt",
        "synthes",
        "token_count",
        "transcri",
        "tts",
        "voice",
        "wake",
    )
    for name in audio.__all__:
        lowered = name.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), name


def test_module_performs_no_io_device_or_model_actions() -> None:
    forbidden_calls = {
        "connect",
        "execute",
        "fork",
        "listen",
        "open",
        "play",
        "record",
        "recv",
        "send",
        "sleep",
        "spawn",
        "synthesize",
        "transcribe",
        "urlopen",
    }
    assert _called_names().isdisjoint(forbidden_calls)


def test_interfaces_are_limited_to_the_provider_abstraction_boundary() -> None:
    protocols = {
        node.name
        for node in _TREE.body
        if isinstance(node, ast.ClassDef)
        and any(getattr(base, "id", None) == "Protocol" for base in node.bases)
    }
    assert protocols == {"AudioProvider", "AudioCaptureStream", "AudioPlaybackStream"}


def test_no_provider_or_stream_is_implemented_in_production_code() -> None:
    """A7.01 ships contracts only: nothing under src/ may implement them.

    This scans class headers textually rather than parsing every module, so the
    guardrail reports a violation instead of dying on unrelated syntax.
    """

    wanted = {"AudioProvider", "AudioCaptureStream", "AudioPlaybackStream"}
    header = re.compile(r"^class\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path == _MODULE:
            continue
        for name, bases in header.findall(path.read_text(encoding="utf-8")):
            declared = {base.strip().rsplit(".", maxsplit=1)[-1] for base in bases.split(",")}
            if declared & wanted:
                offenders.append(f"{path.relative_to(_SRC).as_posix()}:{name}")
    assert offenders == []


def test_stream_identity_reuses_the_canonical_domain_id_owner() -> None:
    """``AudioStreamId`` is defined once, by ``agentx.core.ids``."""

    ids_tree = ast.parse(_IDS.read_text(encoding="utf-8"))
    assert "AudioStreamId" in {
        node.name for node in ids_tree.body if isinstance(node, ast.ClassDef)
    }
    from agentx.core.ids import AudioStreamId

    assert AudioStreamId.__module__ == "agentx.core.ids"
    owners = [
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*.py")
        if "class AudioStreamId" in path.read_text(encoding="utf-8")
    ]
    assert owners == ["core/ids.py"]


def test_audio_contract_carries_no_ambient_mutable_state() -> None:
    """Module level names are constants, types, and functions: never a registry."""

    for node in _TREE.body:
        if isinstance(node, ast.AnnAssign):
            assert ast.unparse(node.annotation).startswith("Final["), ast.unparse(node)
        elif isinstance(node, ast.Assign):
            assert {ast.unparse(target) for target in node.targets} == {"__all__"}
    for name in dir(audio):
        if name.startswith("_"):
            continue
        assert not isinstance(getattr(audio, name), (list, dict, set)), name
    assert not hasattr(audio, "register_provider")
    assert not hasattr(audio, "default_provider")
    assert not hasattr(audio, "current_stream")


def test_audio_bytes_are_treated_as_opaque_data() -> None:
    """Payload is only measured, hashed, and encoded for transport: never read.

    If a future edit starts calling methods on a payload — ``.decode()``,
    ``.hex()``, a frame accessor — audio is being interpreted, and A7.01 has
    stopped being a data boundary.
    """

    def _receiver_is_payload(func: ast.expr) -> bool:
        value = getattr(func, "value", None)
        if isinstance(value, ast.Name):
            return value.id == "payload"
        return isinstance(value, ast.Attribute) and value.attr == "payload"

    called_on_payload = {
        node.func.attr
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _receiver_is_payload(node.func)
    }
    assert called_on_payload == set(), sorted(called_on_payload)
    assert "audioop" not in _import_roots()
    assert "wave" not in _import_roots()
