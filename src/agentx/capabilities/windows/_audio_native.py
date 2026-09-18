"""Isolated WinMM native audio seam for M11.

All ctypes/Win32 audio knowledge lives here. Import is side-effect free: ctypes and
WinMM are loaded lazily only when capture or playback is invoked.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from agentx.core.audio import AudioFailureKind, audio_failure
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationToken
from agentx.core.result import Result

__all__ = ["WinMmAudioSurface"]


def _failure(kind: AudioFailureKind, message: str) -> AgentXError:
    return audio_failure(kind, message=message)


def _is_cancelled(token: CancellationToken) -> bool:
    return token.is_cancelled


class WinMmAudioSurface:
    """Lazy stdlib-only WinMM surface for bounded PCM capture/playback."""

    __slots__ = ()

    @staticmethod
    def _available() -> bool:
        return sys.platform == "win32"

    def capture(
        self,
        *,
        sample_rate_hz: int,
        channel_count: int,
        milliseconds: int,
        cancellation_token: CancellationToken,
    ) -> Result[bytes, AgentXError]:
        if not self._available():
            return Result.failure(
                _failure(AudioFailureKind.PROVIDER_UNAVAILABLE, "WinMM capture requires Windows")
            )
        if _is_cancelled(cancellation_token):
            return Result.failure(_failure(AudioFailureKind.CANCELLED, "audio capture cancelled"))
        if not 10 <= milliseconds <= 1000:
            return Result.failure(
                _failure(
                    AudioFailureKind.CONFIGURATION,
                    "capture interval is outside [10, 1000] ms",
                )
            )

        import ctypes
        from ctypes import wintypes

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", wintypes.WORD),
                ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD),
            ]

        class WAVEHDR(ctypes.Structure):
            _fields_ = [
                ("lpData", ctypes.c_void_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t),
            ]

        winmm: Any = ctypes.WinDLL("winmm", use_last_error=True)
        handle = ctypes.c_void_p()
        block_align = channel_count * 2
        byte_rate = sample_rate_hz * block_align
        byte_count = max(
            block_align,
            (byte_rate * milliseconds // 1000) // block_align * block_align,
        )
        fmt = WAVEFORMATEX(
            1,
            channel_count,
            sample_rate_hz,
            byte_rate,
            block_align,
            16,
            0,
        )
        result = int(winmm.waveInOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0))
        if result != 0:
            return Result.failure(
                _failure(
                    AudioFailureKind.PROVIDER_UNAVAILABLE,
                    "microphone device could not be opened",
                )
            )
        buffer = ctypes.create_string_buffer(byte_count)
        header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), byte_count, 0, 0, 0, 0, None, 0)
        prepared = False
        try:
            if (
                int(winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)))
                != 0
            ):
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "microphone buffer prepare failed")
                )
            prepared = True
            if int(winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))) != 0:
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "microphone buffer enqueue failed")
                )
            if int(winmm.waveInStart(handle)) != 0:
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "microphone capture start failed")
                )
            deadline = time.monotonic() + max(2.0, milliseconds / 1000.0 + 1.0)
            whdr_done = 0x00000001
            while not (int(header.dwFlags) & whdr_done):
                if _is_cancelled(cancellation_token):
                    winmm.waveInReset(handle)
                    return Result.failure(
                        _failure(AudioFailureKind.CANCELLED, "audio capture cancelled")
                    )
                if time.monotonic() >= deadline:
                    winmm.waveInReset(handle)
                    return Result.failure(
                        _failure(AudioFailureKind.TIMEOUT, "microphone capture timed out")
                    )
                time.sleep(0.01)
            recorded = int(header.dwBytesRecorded)
            if recorded <= 0:
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "microphone returned no audio")
                )
            return Result.success(bytes(buffer.raw[:recorded]))
        finally:
            winmm.waveInReset(handle)
            if prepared:
                winmm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            winmm.waveInClose(handle)

    def play(
        self,
        payload: bytes,
        *,
        sample_rate_hz: int,
        channel_count: int,
        cancellation_token: CancellationToken,
    ) -> Result[None, AgentXError]:
        if not self._available():
            return Result.failure(
                _failure(AudioFailureKind.PROVIDER_UNAVAILABLE, "WinMM playback requires Windows")
            )
        if _is_cancelled(cancellation_token):
            return Result.failure(_failure(AudioFailureKind.CANCELLED, "audio playback cancelled"))
        if not payload:
            return Result.failure(
                _failure(AudioFailureKind.EMPTY_PAYLOAD, "audio payload is empty")
            )

        import ctypes
        from ctypes import wintypes

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", wintypes.WORD),
                ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD),
            ]

        class WAVEHDR(ctypes.Structure):
            _fields_ = [
                ("lpData", ctypes.c_void_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t),
            ]

        winmm: Any = ctypes.WinDLL("winmm", use_last_error=True)
        handle = ctypes.c_void_p()
        block_align = channel_count * 2
        byte_rate = sample_rate_hz * block_align
        fmt = WAVEFORMATEX(1, channel_count, sample_rate_hz, byte_rate, block_align, 16, 0)
        result = int(
            winmm.waveOutOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0)
        )
        if result != 0:
            return Result.failure(
                _failure(
                    AudioFailureKind.PROVIDER_UNAVAILABLE,
                    "speaker device could not be opened",
                )
            )
        buffer = ctypes.create_string_buffer(payload)
        header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), len(payload), 0, 0, 0, 0, None, 0)
        prepared = False
        try:
            if (
                int(winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)))
                != 0
            ):
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "speaker buffer prepare failed")
                )
            prepared = True
            if int(winmm.waveOutWrite(handle, ctypes.byref(header), ctypes.sizeof(header))) != 0:
                return Result.failure(
                    _failure(AudioFailureKind.INTERNAL, "speaker playback start failed")
                )
            duration = len(payload) / max(1, byte_rate)
            deadline = time.monotonic() + max(2.0, duration + 1.0)
            whdr_done = 0x00000001
            while not (int(header.dwFlags) & whdr_done):
                if _is_cancelled(cancellation_token):
                    winmm.waveOutReset(handle)
                    return Result.failure(
                        _failure(AudioFailureKind.CANCELLED, "audio playback cancelled")
                    )
                if time.monotonic() >= deadline:
                    winmm.waveOutReset(handle)
                    return Result.failure(
                        _failure(AudioFailureKind.TIMEOUT, "speaker playback timed out")
                    )
                time.sleep(0.01)
            return Result.success(None)
        finally:
            winmm.waveOutReset(handle)
            if prepared:
                winmm.waveOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            winmm.waveOutClose(handle)
