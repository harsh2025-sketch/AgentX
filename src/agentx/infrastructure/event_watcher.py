"""Bounded, non-daemonic event watcher framework (AX-465).

A watcher source reports observations; it never gains authority from doing so.
This framework owns registration, explicit start/stop, bounded one-shot polls,
deduplication, debounce suppression, cooperative cancellation, error isolation,
provenance preservation, task/correlation attribution, and shutdown. It starts
no thread, timer, scheduler, daemon, or unbounded loop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID, uuid4

from agentx.core.execution import CancellationSource, CancellationToken
from agentx.core.knowledge import ProvenanceReference

__all__ = [
    "MAX_WATCHERS",
    "MAX_WATCHER_BATCH",
    "EventWatcherFramework",
    "WatcherEmission",
    "WatcherLifecycle",
    "WatcherObservation",
    "WatcherPollFailure",
    "WatcherPollReport",
    "WatcherRegistration",
    "WatcherSource",
    "WatcherSourceId",
    "WatcherValidationError",
]

MAX_WATCHERS: Final[int] = 64
MAX_WATCHER_BATCH: Final[int] = 256
_MAX_PAYLOAD_BYTES: Final[int] = 65_536
_MAX_TEXT: Final[int] = 512
_MAX_STATE_ENTRIES: Final[int] = 1024
_MAX_WINDOW: Final[timedelta] = timedelta(hours=24)


class WatcherValidationError(ValueError):
    pass


class WatcherLifecycle(StrEnum):
    REGISTERED = "registered"
    RUNNING = "running"
    STOPPED = "stopped"


def _text(value: object, *, field_name: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip() or len(value) > maximum:
        raise WatcherValidationError(f"{field_name} must be bounded non-empty trimmed text")
    if any(character < " " or character == "\x7f" for character in value):
        raise WatcherValidationError(f"{field_name} must not contain control characters")
    return value


def _time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WatcherValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _window(value: object, *, field_name: str) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta")
    if value < timedelta(0) or value > _MAX_WINDOW:
        raise WatcherValidationError(f"{field_name} must be between zero and 24 hours")
    return value


def _freeze_json(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        raise WatcherValidationError("payload nesting exceeds 8 levels")
    if value is None or isinstance(value, bool | int | str):
        if isinstance(value, str) and len(value) > 4096:
            raise WatcherValidationError("payload string exceeds 4096 characters")
        return value
    if isinstance(value, float):
        if not (-float("inf") < value < float("inf")):
            raise WatcherValidationError("payload float must be finite")
        return value
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise WatcherValidationError("payload object has too many fields")
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise WatcherValidationError("payload keys must be bounded non-empty strings")
            result[key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(result)
    if isinstance(value, tuple | list):
        if len(value) > 1024:
            raise WatcherValidationError("payload array is too large")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise WatcherValidationError(f"payload contains unsupported type {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _payload(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WatcherValidationError("payload must be an object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise AssertionError("mapping freeze produced non-mapping")
    encoded = json.dumps(
        _json_value(frozen),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise WatcherValidationError("payload exceeds encoded size bound")
    return frozen


def _fingerprint(observation: WatcherObservation) -> str:
    encoded = json.dumps(
        _json_value(observation.payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{observation.event_key}:{digest}"


@dataclass(frozen=True, slots=True, order=True)
class WatcherSourceId:
    value: str

    def __post_init__(self) -> None:
        _text(self.value, field_name="source_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherObservation:
    """One inert observation returned by a watcher source."""

    source_id: WatcherSourceId
    event_key: str
    observed_at: datetime
    provenance: ProvenanceReference
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, WatcherSourceId):
            raise TypeError("source_id must be WatcherSourceId")
        object.__setattr__(self, "event_key", _text(self.event_key, field_name="event_key"))
        object.__setattr__(self, "observed_at", _time(self.observed_at, field_name="observed_at"))
        if not isinstance(self.provenance, ProvenanceReference):
            raise TypeError("provenance must be ProvenanceReference")
        object.__setattr__(self, "payload", _payload(self.payload))


class WatcherSource(Protocol):
    @property
    def source_id(self) -> WatcherSourceId:
        ...

    def poll(
        self,
        *,
        cancellation: CancellationToken,
        max_items: int,
    ) -> tuple[WatcherObservation, ...]:
        """Return at most ``max_items`` already-observed facts without blocking forever."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherRegistration:
    watcher_id: UUID
    source_id: WatcherSourceId
    correlation_id: UUID
    task_id: str | None = None
    max_batch: int = 64
    dedupe_window: timedelta = timedelta(seconds=1)
    debounce_window: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        for name in ("watcher_id", "correlation_id"):
            value = getattr(self, name)
            if not isinstance(value, UUID) or value.int == 0:
                raise WatcherValidationError(f"{name} must be a non-nil UUID")
        if not isinstance(self.source_id, WatcherSourceId):
            raise TypeError("source_id must be WatcherSourceId")
        if self.task_id is not None:
            _text(self.task_id, field_name="task_id")
        if type(self.max_batch) is not int or not 1 <= self.max_batch <= MAX_WATCHER_BATCH:
            raise WatcherValidationError(f"max_batch must be from 1 to {MAX_WATCHER_BATCH}")
        object.__setattr__(
            self,
            "dedupe_window",
            _window(self.dedupe_window, field_name="dedupe_window"),
        )
        object.__setattr__(
            self,
            "debounce_window",
            _window(self.debounce_window, field_name="debounce_window"),
        )

    @classmethod
    def create(
        cls,
        *,
        source_id: WatcherSourceId,
        correlation_id: UUID,
        task_id: str | None = None,
        max_batch: int = 64,
        dedupe_window: timedelta = timedelta(seconds=1),
        debounce_window: timedelta = timedelta(0),
    ) -> WatcherRegistration:
        return cls(
            watcher_id=uuid4(),
            source_id=source_id,
            correlation_id=correlation_id,
            task_id=task_id,
            max_batch=max_batch,
            dedupe_window=dedupe_window,
            debounce_window=debounce_window,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherEmission:
    watcher_id: UUID
    correlation_id: UUID
    task_id: str | None
    polled_at: datetime
    observation: WatcherObservation


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherPollFailure:
    watcher_id: UUID
    source_id: WatcherSourceId
    error_type: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherPollReport:
    emissions: tuple[WatcherEmission, ...]
    failures: tuple[WatcherPollFailure, ...]
    duplicate_suppressed: int
    debounce_suppressed: int


@dataclass(slots=True)
class _WatcherState:
    registration: WatcherRegistration
    source: WatcherSource
    cancellation: CancellationSource
    lifecycle: WatcherLifecycle = WatcherLifecycle.REGISTERED
    fingerprints: dict[str, datetime] = field(default_factory=dict)
    event_keys: dict[str, datetime] = field(default_factory=dict)


class EventWatcherFramework:
    """Explicitly driven collection of bounded watcher sources."""

    def __init__(self) -> None:
        self._watchers: dict[UUID, _WatcherState] = {}
        self._shutdown = False

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown

    def register(self, registration: WatcherRegistration, source: WatcherSource) -> None:
        if self._shutdown:
            raise WatcherValidationError("watcher framework is shut down")
        if not isinstance(registration, WatcherRegistration):
            raise TypeError("registration must be WatcherRegistration")
        if len(self._watchers) >= MAX_WATCHERS:
            raise WatcherValidationError("watcher registry is full")
        if registration.watcher_id in self._watchers:
            raise WatcherValidationError("watcher_id is already registered")
        if source.source_id != registration.source_id:
            raise WatcherValidationError("source identity does not match registration")
        self._watchers[registration.watcher_id] = _WatcherState(
            registration=registration,
            source=source,
            cancellation=CancellationSource(),
        )

    def lifecycle(self, watcher_id: UUID) -> WatcherLifecycle:
        return self._state(watcher_id).lifecycle

    def start(self, watcher_id: UUID) -> None:
        state = self._state(watcher_id)
        if state.lifecycle is not WatcherLifecycle.REGISTERED:
            raise WatcherValidationError("only registered watchers can start")
        state.lifecycle = WatcherLifecycle.RUNNING

    def stop(self, watcher_id: UUID, *, reason: str | None = None) -> None:
        state = self._state(watcher_id)
        if state.lifecycle is WatcherLifecycle.STOPPED:
            return
        state.cancellation.request_cancellation(reason)
        state.lifecycle = WatcherLifecycle.STOPPED

    def poll_once(self, watcher_id: UUID, *, polled_at: datetime) -> WatcherPollReport:
        if self._shutdown:
            raise WatcherValidationError("watcher framework is shut down")
        state = self._state(watcher_id)
        if state.lifecycle is not WatcherLifecycle.RUNNING:
            raise WatcherValidationError("watcher must be running before poll")
        moment = _time(polled_at, field_name="polled_at")
        try:
            observations = state.source.poll(
                cancellation=state.cancellation.token,
                max_items=state.registration.max_batch,
            )
            if not isinstance(observations, tuple):
                raise TypeError("watcher source poll must return a tuple")
            if len(observations) > state.registration.max_batch:
                raise WatcherValidationError("watcher source exceeded max_batch")
            emissions, duplicate_count, debounce_count = self._filter(
                state,
                observations,
                polled_at=moment,
            )
            return WatcherPollReport(
                emissions=emissions,
                failures=(),
                duplicate_suppressed=duplicate_count,
                debounce_suppressed=debounce_count,
            )
        except Exception as exc:
            return WatcherPollReport(
                emissions=(),
                failures=(
                    WatcherPollFailure(
                        watcher_id=state.registration.watcher_id,
                        source_id=state.registration.source_id,
                        error_type=type(exc).__name__,
                        message=str(exc)[:1024],
                    ),
                ),
                duplicate_suppressed=0,
                debounce_suppressed=0,
            )

    def poll_all_once(self, *, polled_at: datetime) -> WatcherPollReport:
        moment = _time(polled_at, field_name="polled_at")
        emissions: list[WatcherEmission] = []
        failures: list[WatcherPollFailure] = []
        duplicates = 0
        debounced = 0
        running_ids = tuple(
            watcher_id
            for watcher_id, state in self._watchers.items()
            if state.lifecycle is WatcherLifecycle.RUNNING
        )
        for watcher_id in running_ids:
            report = self.poll_once(watcher_id, polled_at=moment)
            emissions.extend(report.emissions)
            failures.extend(report.failures)
            duplicates += report.duplicate_suppressed
            debounced += report.debounce_suppressed
        return WatcherPollReport(
            emissions=tuple(emissions),
            failures=tuple(failures),
            duplicate_suppressed=duplicates,
            debounce_suppressed=debounced,
        )

    def shutdown(self, *, reason: str = "watcher framework shutdown") -> None:
        if self._shutdown:
            return
        for state in tuple(self._watchers.values()):
            if state.lifecycle is not WatcherLifecycle.STOPPED:
                state.cancellation.request_cancellation(reason)
                state.lifecycle = WatcherLifecycle.STOPPED
        self._shutdown = True

    def _state(self, watcher_id: UUID) -> _WatcherState:
        if not isinstance(watcher_id, UUID):
            raise TypeError("watcher_id must be UUID")
        try:
            return self._watchers[watcher_id]
        except KeyError as exc:
            raise WatcherValidationError("unknown watcher_id") from exc

    def _filter(
        self,
        state: _WatcherState,
        observations: tuple[WatcherObservation, ...],
        *,
        polled_at: datetime,
    ) -> tuple[tuple[WatcherEmission, ...], int, int]:
        registration = state.registration
        self._prune(state, polled_at=polled_at)
        emissions: list[WatcherEmission] = []
        duplicates = 0
        debounced = 0
        for observation in observations:
            if not isinstance(observation, WatcherObservation):
                raise TypeError("source returned a non-WatcherObservation")
            if observation.source_id != registration.source_id:
                raise WatcherValidationError("observation source does not match registration")
            fingerprint = _fingerprint(observation)
            duplicate_at = state.fingerprints.get(fingerprint)
            if duplicate_at is not None and polled_at - duplicate_at < registration.dedupe_window:
                duplicates += 1
                continue
            key_at = state.event_keys.get(observation.event_key)
            if key_at is not None and polled_at - key_at < registration.debounce_window:
                debounced += 1
                continue
            state.fingerprints[fingerprint] = polled_at
            state.event_keys[observation.event_key] = polled_at
            emissions.append(
                WatcherEmission(
                    watcher_id=registration.watcher_id,
                    correlation_id=registration.correlation_id,
                    task_id=registration.task_id,
                    polled_at=polled_at,
                    observation=observation,
                )
            )
        return tuple(emissions), duplicates, debounced

    @staticmethod
    def _prune(state: _WatcherState, *, polled_at: datetime) -> None:
        window = max(state.registration.dedupe_window, state.registration.debounce_window)
        cutoff = polled_at - window
        state.fingerprints = {
            key: seen for key, seen in state.fingerprints.items() if seen >= cutoff
        }
        state.event_keys = {key: seen for key, seen in state.event_keys.items() if seen >= cutoff}
        for values in (state.fingerprints, state.event_keys):
            if len(values) > _MAX_STATE_ENTRIES:
                oldest = sorted(values.items(), key=lambda item: item[1])[
                    : len(values) - _MAX_STATE_ENTRIES
                ]
                for key, _ in oldest:
                    values.pop(key, None)
