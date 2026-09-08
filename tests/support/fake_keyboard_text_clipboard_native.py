"""Deterministic fake native ports for the N2.23 keyboard/text/clipboard seam.

These fixtures perform no OS action of any kind: they return preloaded raw
outcomes and record every port method call so tests can prove that the
capabilities invoke exactly the narrow seam methods they declare and nothing
else. They exist so every N2.23 behaviour is testable deterministically on
Windows, Linux, and macOS alike, with hostile, oversized, or misreporting
"native" outcomes that would be unreliable to reproduce against a real
desktop.

They are deliberately *not* part of the ``agentx`` package: N2.23 ships the
governed capability layer and its narrow ports, not test doubles.
"""

from __future__ import annotations

from agentx.capabilities.windows.keyboard_text_clipboard import (
    KeyChord,
    RawClipboardClear,
    RawClipboardText,
    RawClipboardWrite,
    RawKeySend,
    RawTextSend,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result


class FakeKeyboardPort:
    """Recording fake of the narrow keyboard input port."""

    def __init__(self) -> None:
        self.text_sends: list[str] = []
        self.key_sends: list[tuple[KeyChord, ...]] = []
        self.text_outcome: Result[RawTextSend, AgentXError] | None = None
        self.keys_outcome: Result[RawKeySend, AgentXError] | None = None

    @property
    def call_count(self) -> int:
        return len(self.text_sends) + len(self.key_sends)

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        self.text_sends.append(text)
        if self.text_outcome is not None:
            return self.text_outcome
        return Result.success(RawTextSend(character_count=len(text)))

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        self.key_sends.append(tuple(chords))
        if self.keys_outcome is not None:
            return self.keys_outcome
        return Result.success(RawKeySend(chord_count=len(chords)))


class FakeClipboardPort:
    """Recording fake of the narrow clipboard port.

    The fake keeps an explicit in-memory clipboard text state so tests can
    exercise read-after-write and clear semantics deterministically: writing
    replaces the state, clearing empties it, and a read of the absent text
    format reports ``None``.
    """

    def __init__(self, *, clip_text: str | None = None) -> None:
        self.clip_text: str | None = clip_text
        self.reads: int = 0
        self.writes: list[str] = []
        self.clears: int = 0
        self.read_outcome: Result[RawClipboardText, AgentXError] | None = None
        self.write_outcome: Result[RawClipboardWrite, AgentXError] | None = None
        self.clear_outcome: Result[RawClipboardClear, AgentXError] | None = None

    @property
    def call_count(self) -> int:
        return self.reads + len(self.writes) + self.clears

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        self.reads += 1
        if self.read_outcome is not None:
            return self.read_outcome
        return Result.success(RawClipboardText(text=self.clip_text))

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        self.writes.append(text)
        if self.write_outcome is not None:
            return self.write_outcome
        self.clip_text = text
        return Result.success(RawClipboardWrite(character_count=len(text)))

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        self.clears += 1
        if self.clear_outcome is not None:
            return self.clear_outcome
        previous_was_empty = self.clip_text is None
        self.clip_text = None
        return Result.success(RawClipboardClear(previous_was_empty=previous_was_empty))
