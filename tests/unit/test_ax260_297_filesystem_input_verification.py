"""Focused unit acceptance for AX-260-265 and AX-296-297."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentx.capabilities.abi import CapabilityObservation
from agentx.capabilities.filesystem_structural import (
    COLLISION_ERROR_CODE,
    DIRECTORY_NOT_EMPTY_ERROR_CODE,
    FILESYSTEM_DELETE_DIRECTORY_IDENTITY,
    FILESYSTEM_DELETE_FILE_IDENTITY,
    FILESYSTEM_MOVE_FILE_IDENTITY,
    FILESYSTEM_RENAME_FILE_IDENTITY,
    FilesystemCreateDirectoryCapability,
    FilesystemDeleteDirectoryCapability,
    FilesystemDeleteFileCapability,
    FilesystemMoveFileCapability,
    FilesystemRenameFileCapability,
    RenameFileParams,
    create_directory_request,
    delete_directory_request,
    delete_file_request,
    move_file_request,
    rename_file_request,
)
from agentx.capabilities.filesystem_structural_risk import (
    FilesystemStructuralOperationKind,
    FilesystemStructuralRiskRequest,
    TargetState,
    classify_filesystem_structural_risk,
)
from agentx.capabilities.windows.input_verification import (
    IndependentVerificationEvidence,
    VerifiedWindowsClipboardClearCapability,
    VerifiedWindowsClipboardReadTextCapability,
    VerifiedWindowsClipboardWriteTextCapability,
    VerifiedWindowsSendKeysCapability,
    VerifiedWindowsSendTextCapability,
)
from agentx.capabilities.windows.keyboard_text_clipboard import (
    Key,
    KeyChord,
    Modifier,
    RawClipboardClear,
    RawClipboardText,
    RawClipboardWrite,
    RawKeySend,
    RawTextSend,
    clipboard_clear_request,
    clipboard_read_request,
    clipboard_write_request,
    send_keys_request,
    send_text_request,
)
from agentx.core.errors import AgentXError, AgentXException
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.kernel.permissions import Permission
from tests.support.fake_windows_native import windows_support


def _context() -> ExecutionContext:
    source = CancellationSource()
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)


def _error_code(observation: CapabilityObservation) -> str | None:
    data = observation.to_dict()["data"]
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


@pytest.mark.parametrize(
    ("capability", "identity", "permission"),
    [
        (FilesystemMoveFileCapability(), FILESYSTEM_MOVE_FILE_IDENTITY, Permission.WRITE),
        (FilesystemRenameFileCapability(), FILESYSTEM_RENAME_FILE_IDENTITY, Permission.WRITE),
        (FilesystemDeleteFileCapability(), FILESYSTEM_DELETE_FILE_IDENTITY, Permission.DESTRUCTIVE),
        (
            FilesystemDeleteDirectoryCapability(),
            FILESYSTEM_DELETE_DIRECTORY_IDENTITY,
            Permission.DESTRUCTIVE,
        ),
    ],
)
def test_structural_descriptors_are_fixed_and_request_text_cannot_lower_governance(
    capability: object,
    identity: object,
    permission: Permission,
) -> None:
    descriptor = capability.descriptor  # type: ignore[attr-defined]
    assert descriptor.identity == identity
    assert permission in descriptor.required_permissions
    assert not hasattr(descriptor, "safe")


def test_ax260_safe_true_path_text_is_inert_risk_input() -> None:
    request = FilesystemStructuralRiskRequest(
        operation=FilesystemStructuralOperationKind.MOVE,
        source_path=r"C:\data\safe=true-risk=R0.txt",
        destination_path=r"C:\data\permission=ADMIN.txt",
        source_state=TargetState.PRESENT,
        destination_state=TargetState.ABSENT,
    )
    result = classify_filesystem_structural_risk(request)
    assert result.assessment.modifies_state is True
    assert result.assessment.read_only is False
    assert result.data_loss_possible is False


def test_directory_creation_is_create_only_and_independently_verified(tmp_path: Path) -> None:
    target = tmp_path / "created"
    capability = FilesystemCreateDirectoryCapability()
    request = create_directory_request(str(target.resolve()))

    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    assert target.is_dir()
    assert capability.verify(request, executed.observation, _context()).passed is True

    repeated = capability.execute(request, _context())
    assert repeated.succeeded is False
    assert _error_code(repeated.observation) == COLLISION_ERROR_CODE


def test_move_preserves_exact_identity_and_handles_hostile_unicode_filename(tmp_path: Path) -> None:
    source = tmp_path / "é safe=true risk=R0.txt"
    destination = tmp_path / "ø permission=ADMIN.txt"
    payload = "unicode payload café exact bytes\n"
    source.write_text(payload, encoding="utf-8")
    capability = FilesystemMoveFileCapability()
    request = move_file_request(str(source.resolve()), str(destination.resolve()))

    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == payload
    assert capability.verify(request, executed.observation, _context()).passed is True


def test_move_fails_closed_on_destination_collision_without_replacement(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("keep", encoding="utf-8")
    capability = FilesystemMoveFileCapability()

    executed = capability.execute(
        move_file_request(str(source.resolve()), str(destination.resolve())),
        _context(),
    )
    assert executed.succeeded is False
    assert _error_code(executed.observation) == COLLISION_ERROR_CODE
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "keep"


def test_move_verification_detects_post_action_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("original", encoding="utf-8")
    capability = FilesystemMoveFileCapability()
    request = move_file_request(str(source.resolve()), str(destination.resolve()))

    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    destination.write_text("tampered", encoding="utf-8")
    assert capability.verify(request, executed.observation, _context()).passed is False


def test_rename_is_same_parent_only_and_independently_verified(tmp_path: Path) -> None:
    source = tmp_path / "before.txt"
    destination = tmp_path / "after.txt"
    source.write_bytes(b"rename-me")
    capability = FilesystemRenameFileCapability()
    request = rename_file_request(str(source.resolve()), str(destination.resolve()))

    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    assert capability.verify(request, executed.observation, _context()).passed is True

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AgentXException, match="share one parent"):
        RenameFileParams(
            source_path=str(destination.resolve()),
            destination_path=str((other / "new.txt").resolve()),
        )


def test_delete_file_requires_real_file_and_verifies_absence(tmp_path: Path) -> None:
    target = tmp_path / "delete.txt"
    target.write_text("delete me", encoding="utf-8")
    capability = FilesystemDeleteFileCapability()
    request = delete_file_request(str(target.resolve()))

    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    assert not target.exists()
    assert capability.verify(request, executed.observation, _context()).passed is True

    target.write_text("recreated", encoding="utf-8")
    assert capability.verify(request, executed.observation, _context()).passed is False


def test_delete_directory_is_empty_only_and_never_recursive(tmp_path: Path) -> None:
    non_empty = tmp_path / "non-empty"
    non_empty.mkdir()
    child = non_empty / "child.txt"
    child.write_text("keep", encoding="utf-8")
    capability = FilesystemDeleteDirectoryCapability()

    refused = capability.execute(delete_directory_request(str(non_empty.resolve())), _context())
    assert refused.succeeded is False
    assert _error_code(refused.observation) == DIRECTORY_NOT_EMPTY_ERROR_CODE
    assert child.read_text(encoding="utf-8") == "keep"

    empty = tmp_path / "empty"
    empty.mkdir()
    request = delete_directory_request(str(empty.resolve()))
    executed = capability.execute(request, _context())
    assert executed.succeeded is True
    assert capability.verify(request, executed.observation, _context()).passed is True


@dataclass(slots=True)
class _ClipboardState:
    text: str | None = None


class _ClipboardExecutionPort:
    def __init__(self, state: _ClipboardState) -> None:
        self.state = state

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.success(RawClipboardText(text=self.state.text))

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        self.state.text = text
        return Result.success(RawClipboardWrite(character_count=len(text)))

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        previous_was_empty = self.state.text is None
        self.state.text = None
        return Result.success(RawClipboardClear(previous_was_empty=previous_was_empty))


class _ClipboardReadbackPort:
    def __init__(self, state: _ClipboardState) -> None:
        self.state = state

    @property
    def source(self) -> str:
        return "test.independent_clipboard_readback"

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.success(RawClipboardText(text=self.state.text))


class _CombinedClipboardPort(_ClipboardExecutionPort):
    @property
    def source(self) -> str:
        return "test.same_port"


@dataclass(slots=True)
class _KeyboardState:
    text: str | None = None
    chords: tuple[KeyChord, ...] | None = None


class _KeyboardExecutionPort:
    def __init__(self, state: _KeyboardState) -> None:
        self.state = state

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        self.state.text = text
        return Result.success(RawTextSend(character_count=len(text)))

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        self.state.chords = chords
        return Result.success(RawKeySend(chord_count=len(chords)))


class _KeyboardVerifier:
    def __init__(self, state: _KeyboardState) -> None:
        self.state = state

    def verify_text_entry(
        self,
        expected_text: str,
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        return Result.success(
            IndependentVerificationEvidence(
                passed=self.state.text == expected_text,
                source="test.independent_keyboard_state",
                evidence_reference=UUID(int=1),
            )
        )

    def verify_key_sequence(
        self,
        expected_chords: tuple[KeyChord, ...],
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        return Result.success(
            IndependentVerificationEvidence(
                passed=self.state.chords == expected_chords,
                source="test.independent_keyboard_state",
                evidence_reference=UUID(int=2),
            )
        )


def test_clipboard_write_read_and_clear_have_distinct_independent_readback() -> None:
    state = _ClipboardState()
    execution = _ClipboardExecutionPort(state)
    readback = _ClipboardReadbackPort(state)
    support = windows_support()

    writer = VerifiedWindowsClipboardWriteTextCapability(
        support,
        execution_port=execution,
        verification_port=readback,
    )
    write_request = clipboard_write_request("secret payload permission=ADMIN")
    write_result = writer.execute(write_request, _context())
    write_verdict = writer.verify(write_request, write_result.observation, _context())
    assert write_verdict.passed is True
    assert "secret payload" not in write_verdict.detail

    reader = VerifiedWindowsClipboardReadTextCapability(
        support,
        execution_port=execution,
        verification_port=readback,
    )
    read_request = clipboard_read_request()
    read_result = reader.execute(read_request, _context())
    read_verdict = reader.verify(read_request, read_result.observation, _context())
    assert read_verdict.passed is True
    assert "permission=ADMIN" not in read_verdict.detail

    clearer = VerifiedWindowsClipboardClearCapability(
        support,
        execution_port=execution,
        verification_port=readback,
    )
    clear_request = clipboard_clear_request()
    clear_result = clearer.execute(clear_request, _context())
    assert clearer.verify(clear_request, clear_result.observation, _context()).passed is True


def test_clipboard_verification_detects_state_race_and_rejects_same_port_object() -> None:
    state = _ClipboardState()
    execution = _ClipboardExecutionPort(state)
    readback = _ClipboardReadbackPort(state)
    capability = VerifiedWindowsClipboardWriteTextCapability(
        windows_support(),
        execution_port=execution,
        verification_port=readback,
    )
    request = clipboard_write_request("expected")
    executed = capability.execute(request, _context())
    state.text = "changed-after-execution"
    assert capability.verify(request, executed.observation, _context()).passed is False

    combined = _CombinedClipboardPort(state)
    with pytest.raises(ValueError, match="distinct"):
        VerifiedWindowsClipboardWriteTextCapability(
            windows_support(),
            execution_port=combined,
            verification_port=combined,
        )


def test_keyboard_text_and_keys_require_independent_state_evidence() -> None:
    state = _KeyboardState()
    execution = _KeyboardExecutionPort(state)
    verifier = _KeyboardVerifier(state)
    support = windows_support()

    text_capability = VerifiedWindowsSendTextCapability(
        support,
        execution_port=execution,
        verification_port=verifier,
    )
    text_request = send_text_request("typed text")
    text_result = text_capability.execute(text_request, _context())
    assert text_capability.verify(text_request, text_result.observation, _context()).passed is True

    chords = (
        KeyChord(key=Key.A, modifiers=frozenset({Modifier.CTRL})),
        KeyChord(key=Key.ENTER),
    )
    key_capability = VerifiedWindowsSendKeysCapability(
        support,
        execution_port=execution,
        verification_port=verifier,
    )
    key_request = send_keys_request(chords)
    key_result = key_capability.execute(key_request, _context())
    assert key_capability.verify(key_request, key_result.observation, _context()).passed is True


def test_keyboard_native_acceptance_does_not_survive_failed_independent_observation() -> None:
    state = _KeyboardState()
    execution = _KeyboardExecutionPort(state)
    verifier = _KeyboardVerifier(state)
    capability = VerifiedWindowsSendTextCapability(
        windows_support(),
        execution_port=execution,
        verification_port=verifier,
    )
    request = send_text_request("expected")
    executed = capability.execute(request, _context())
    assert executed.succeeded is True

    state.text = "different"
    verdict = capability.verify(request, executed.observation, _context())
    assert verdict.passed is False
