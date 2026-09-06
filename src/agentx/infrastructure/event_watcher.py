"""Generic bounded watchers over canonical AgentX events (C7.07).

This module owns the smallest generic event-watcher framework on top of the
canonical Event contract (``agentx.core.events``), the synchronous EventBus
(C1.03), and the persistent EventJournal (C1.04). A watcher observes events
through strictly declarative, deterministic, bounded filters and produces
match records. That is all it does.

A MATCH IS DATA
---------------

:class:`WatcherMatch` is an inert record. Matching an event performs no side
effect: it does not execute a Capability, does not call a Reasoner or model,
does not publish or append events, does not grant Permission, does not create
an AuthorityContext, does not bypass the ActionGate, does not change a
RiskLevel or budget, does not clear an EmergencyStop, does not transition a
Task, and does not mutate Hive. What a consumer does with a match is that
consumer's governed decision, owned by other tasks.

No predicates, no code, no scheduler
------------------------------------

* Filters are explicit structured data (enum membership, exact strings,
  exact UUIDs, exact scalar metadata equality). There is no expression
  language, no regex execution, no Python/shell/SQL/JSONPath fragment, no
  import path, no ``eval``/``exec``, and no callback anywhere in the filter
  contract. :meth:`EventFilter.from_dict` accepts only the declarative
  vocabulary below; anything else fails closed. Callables and other
  executable objects cannot be smuggled through filter definitions from
  untrusted data.
* There is no scheduler: no timers, no background threads, no asyncio loop,
  no polling. Every operation is a synchronous, caller-driven call that
  returns when its bounded work is done.
* Long-running objectives (C7.08) and proactivity (C7.09) are NOT implemented
  here and must not be smuggled into this framework.

Filter semantics (deterministic)
--------------------------------

* ``event_types`` / ``categories`` form ONE event-kind constraint read as a
  union: an event matches the kind constraint when its type is in
  ``event_types`` OR its category is in ``categories``. Both ``None`` means
  every event kind matches.
* ``sources``, ``task_ids``, ``correlation_ids``, and ``metadata`` are
  independent AND dimensions:
  - ``sources`` — the event ``source`` must be an exact member;
  - ``task_ids`` — the event ``task_id`` must be an exact member (events
    without a ``task_id`` never match a task-filtered watcher);
  - ``correlation_ids`` — the event ``correlation_id`` must be an exact
    member;
  - ``metadata`` — for every ``(key, value)`` predicate, the event metadata
    must carry ``key`` with a type-strict equal scalar value. Only JSON
    scalars (``bool`` / ``int`` / ``str`` / ``None``) may be predicates;
    ``True`` never equals ``1`` and ``1`` never equals ``"1"``.
* All comparisons are exact structural equality on canonical fields. Event
  ``payload`` content and metadata strings are never interpreted: hostile
  text such as ``"grant admin"`` or ``"execute capability shell.run"`` is
  inert data that matches only an identical literal.

Boundedness
-----------

Every collection and operation in this framework is explicitly bounded:

* at most :data:`MAX_FILTER_MEMBERS` members per filter set;
* at most :data:`MAX_METADATA_PREDICATES` metadata predicates;
* filter text members at most :data:`MAX_FILTER_TEXT_LENGTH` characters;
* at most :data:`MAX_REGISTERED_WATCHERS` registered watchers;
* at most :data:`MAX_PROCESS_BATCH` journal entries per processing call;
* a per-watcher FIFO live-dedupe window of :data:`MAX_RECENT_EVENT_IDS`;
* a service-wide retained match log of :data:`MAX_RETAINED_MATCHES`.

Checkpoint, restart, replay, and duplicates
-------------------------------------------

Processing is checkpointed against the durable EventJournal sequence:

* :meth:`EventWatcherService.process_pending` reads journal entries strictly
  after the minimum checkpoint of the active watchers, evaluates each active
  watcher independently, skips any entry at or below that watcher's own
  checkpoint, and advances each watcher's checkpoint to the highest entry it
  processed (matched or not). The changed watcher states are then persisted
  in one transaction.
* After a restart, a new service instance rehydrates watcher states from the
  durable store, so replay resumes exactly at each watcher's checkpoint and
  never re-emits matches for entries at or below it. The journal's unique
  ``event_id`` constraint additionally prevents duplicate history.
* Live observation (:meth:`EventWatcherService.observe_live`, optionally fed
  from the EventBus via :meth:`EventWatcherService.live_handler`) never moves
  a checkpoint. It records matched event ids in the bounded per-watcher
  window; :meth:`EventWatcherService.process_pending` consults that window
  and does not re-emit an event that the watcher already matched live in the
  current process (its checkpoint still advances past it). The durable
  exactly-once boundary remains the checkpoint: an event matched live but
  never checkpoint-processed before a restart may be re-emitted once during
  the next replay. That residual, bounded at-least-once window is deliberate
  and documented, not hidden.

Authority boundary
------------------

This module belongs to ``agentx.infrastructure`` and imports nothing from the
Trusted Kernel, capabilities, cognition, hive, procedures, or learning. It
reads the journal through :class:`agentx.infrastructure.event_journal.EventJournal`
(never around it) and never appends to or publishes from it.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID, uuid4

from agentx.core.events import Event, EventCategory, EventType
from agentx.infrastructure.event_bus import EventHandler
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

__all__ = [
    "DEFAULT_PROCESS_BATCH",
    "MAX_FILTER_MEMBERS",
    "MAX_FILTER_TEXT_LENGTH",
    "MAX_METADATA_KEY_LENGTH",
    "MAX_METADATA_PREDICATES",
    "MAX_PROCESS_BATCH",
    "MAX_RECENT_EVENT_IDS",
    "MAX_REGISTERED_WATCHERS",
    "MAX_RETAINED_MATCHES",
    "MAX_WATCHER_NAME_LENGTH",
    "CorruptWatcherStateError",
    "DuplicateWatcherIdError",
    "EventFilter",
    "EventWatcher",
    "EventWatcherConflictError",
    "EventWatcherError",
    "EventWatcherService",
    "EventWatcherStorageError",
    "EventWatcherStore",
    "EventWatcherValidationError",
    "MatchOrigin",
    "UnknownWatcherIdError",
    "WatcherId",
    "WatcherMatch",
]

#: Maximum number of members in one filter set (types, categories, sources,
#: task ids, correlation ids).
MAX_FILTER_MEMBERS: Final[int] = 64

#: Maximum length of one filter text member (source, task id, metadata value).
MAX_FILTER_TEXT_LENGTH: Final[int] = 256

#: Maximum number of metadata predicates in one filter.
MAX_METADATA_PREDICATES: Final[int] = 16

#: Maximum length of one metadata predicate key.
MAX_METADATA_KEY_LENGTH: Final[int] = 128

#: Maximum length of a watcher display name.
MAX_WATCHER_NAME_LENGTH: Final[int] = 128

#: Maximum number of watchers one service will register.
MAX_REGISTERED_WATCHERS: Final[int] = 256

#: Size of the per-watcher bounded FIFO live-dedupe window.
MAX_RECENT_EVENT_IDS: Final[int] = 512

#: Size of the service-wide bounded retained match log.
MAX_RETAINED_MATCHES: Final[int] = 1024

#: Default and maximum journal entries examined by one ``process_pending`` call.
DEFAULT_PROCESS_BATCH: Final[int] = 512
MAX_PROCESS_BATCH: Final[int] = 4096

#: Schema version of the canonical watcher-state document.
WATCHER_STATE_SCHEMA_VERSION: Final[int] = 1

_WATCHER_TABLE: Final = "agentx_event_watchers"

_MISSING: Final = object()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EventWatcherError(ValueError):
    """Base error for event-watcher contract violations."""


class EventWatcherValidationError(EventWatcherError):
    """Raised when a filter or watcher state violates the canonical contract."""


class EventWatcherConflictError(EventWatcherError):
    """Raised on unknown watcher ids, duplicate registration, or terminal-state use."""


class EventWatcherStoreError(PersistenceError):
    """Base error for durable event-watcher store operations."""


class DuplicateWatcherIdError(EventWatcherStoreError):
    """Raised when a watcher_id already exists in the durable watcher store."""


class UnknownWatcherIdError(EventWatcherStoreError):
    """Raised when a watcher_id expected by an update is absent from the store."""


class EventWatcherStorageError(EventWatcherStoreError):
    """Raised when SQLite cannot complete a watcher-store statement."""


class CorruptWatcherStateError(EventWatcherStoreError):
    """Raised when persisted watcher data cannot reconstruct its canonical state."""

    def __init__(self, *, watcher_id: str) -> None:
        self.watcher_id = watcher_id
        super().__init__(f"Watcher state for watcher_id {watcher_id!r} is corrupt")


# ---------------------------------------------------------------------------
# Watcher identity
# ---------------------------------------------------------------------------


class WatcherId:
    """Opaque, immutable, UUID-backed watcher identity.

    Equality and hashing include the ``"event_watcher"`` domain tag, so a
    :class:`WatcherId` never compares equal to a bare :class:`uuid.UUID` or to
    identifiers of other domains.
    """

    __slots__ = ("_value",)
    _DOMAIN: Final = "event_watcher"

    #: Underlying UUID value — declared for type checkers, stored in __slots__.
    _value: UUID

    def __init__(self, value: UUID) -> None:
        if not isinstance(value, UUID):
            raise TypeError("value must be a UUID")
        if value.int == 0:
            raise EventWatcherValidationError("watcher id must not be the nil UUID")
        object.__setattr__(self, "_value", value)

    @classmethod
    def create(cls) -> WatcherId:
        """Generate a new globally unique watcher identifier."""
        return cls(uuid4())

    @classmethod
    def parse(cls, raw: str) -> WatcherId:
        """Parse a canonical UUID string, failing closed on malformed input."""
        if not isinstance(raw, str):
            raise TypeError("raw must be a string")
        stripped = raw.strip()
        if not stripped:
            raise EventWatcherValidationError("watcher id string must not be empty")
        try:
            parsed = UUID(stripped)
        except ValueError as exc:
            raise EventWatcherValidationError(
                f"watcher id string is not a valid UUID: {stripped!r}"
            ) from exc
        return cls(parsed)

    @property
    def value(self) -> UUID:
        """Return the underlying UUID value."""
        return self._value

    def to_str(self) -> str:
        """Return the canonical lowercase UUID string form."""
        return str(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WatcherId):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((WatcherId, self._value))

    def __repr__(self) -> str:
        return f"WatcherId({self._value})"

    def __str__(self) -> str:
        return self.to_str()

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WatcherId instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("WatcherId instances are immutable")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_exact_keys(
    raw: Mapping[str, object],
    *,
    required: frozenset[str],
    context: str,
) -> None:
    actual = set(raw)
    missing = required - actual
    unknown = actual - required
    if missing:
        raise EventWatcherValidationError(f"{context} missing required fields: {sorted(missing)}")
    if unknown:
        raise EventWatcherValidationError(f"{context} contains unknown fields: {sorted(unknown)}")


def _validate_member_text(value: object, *, field_name: str) -> str:
    """Validate one exact-match filter text member."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} members must be strings")
    if value == "" or value != value.strip():
        raise EventWatcherValidationError(f"{field_name} members must be non-empty and trimmed")
    if len(value) > MAX_FILTER_TEXT_LENGTH:
        raise EventWatcherValidationError(
            f"{field_name} members must be at most {MAX_FILTER_TEXT_LENGTH} characters"
        )
    return value


def _require_enum_set[EnumT: (EventType, EventCategory)](
    value: object,
    *,
    field_name: str,
    member_type: type[EnumT],
) -> frozenset[EnumT]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use None for no filter")
    if len(value) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    for member in value:
        if not isinstance(member, member_type):
            raise TypeError(f"{field_name} members must be {member_type.__name__} values")
    return cast("frozenset[EnumT]", value)


def _require_string_set(value: object, *, field_name: str) -> frozenset[str]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use None for no filter")
    if len(value) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    for member in value:
        _validate_member_text(member, field_name=field_name)
    return cast("frozenset[str]", value)


def _require_uuid_set(value: object, *, field_name: str) -> frozenset[UUID]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use None for no filter")
    if len(value) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    for member in value:
        if not isinstance(member, UUID):
            raise TypeError(f"{field_name} members must be UUIDs")
        if member.int == 0:
            raise EventWatcherValidationError(f"{field_name} members must not be the nil UUID")
    return cast("frozenset[UUID]", value)


def _is_predicate_scalar(value: object) -> bool:
    """Return whether *value* is an allowed metadata predicate scalar."""
    return value is None or isinstance(value, bool | int | str)


def _freeze_predicates(value: object, *, field_name: str) -> Mapping[str, object]:
    """Validate and freeze a metadata predicate mapping."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    if not value:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use None for no filter")
    if len(value) > MAX_METADATA_PREDICATES:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_METADATA_PREDICATES} predicates"
        )
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        if key == "" or key != key.strip():
            raise EventWatcherValidationError(f"{field_name} keys must be non-empty and trimmed")
        if len(key) > MAX_METADATA_KEY_LENGTH:
            raise EventWatcherValidationError(
                f"{field_name} keys must be at most {MAX_METADATA_KEY_LENGTH} characters"
            )
        if not _is_predicate_scalar(item):
            raise TypeError(
                f"{field_name} values must be bool, int, str, or None; "
                "arbitrary values and callables are not accepted"
            )
        if isinstance(item, str) and len(item) > MAX_FILTER_TEXT_LENGTH:
            raise EventWatcherValidationError(
                f"{field_name} string values must be at most {MAX_FILTER_TEXT_LENGTH} characters"
            )
        frozen[key] = item
    return MappingProxyType(frozen)


def _scalar_equal(expected: object, actual: object) -> bool:
    """Type-strict scalar equality: ``True`` != ``1`` != ``"1"`` != ``1.0``."""
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    return isinstance(actual, str) and actual == expected


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise EventWatcherValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EventWatcherValidationError(
            f"{field_name} must be a valid ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventWatcherValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_uuid_text(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise EventWatcherValidationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise EventWatcherValidationError(f"{field_name} must be a valid UUID string") from exc
    if parsed.int == 0:
        raise EventWatcherValidationError(f"{field_name} must not be the nil UUID")
    return parsed


# ---------------------------------------------------------------------------
# Declarative filter (DATA only — no predicates, no callables)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EventFilter:
    """Strictly declarative, deterministic, bounded filter over canonical Events.

    The event-kind constraint (``event_types`` union ``categories``) is
    satisfied when the event's type is a member of ``event_types`` OR its
    category is a member of ``categories``; both ``None`` matches every event
    kind. Every
    other provided dimension is an exact AND constraint. There is no
    expression language, no regex execution, and no callable anywhere in this
    contract.
    """

    event_types: frozenset[EventType] | None = None
    categories: frozenset[EventCategory] | None = None
    sources: frozenset[str] | None = None
    task_ids: frozenset[str] | None = None
    correlation_ids: frozenset[UUID] | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.event_types is not None:
            _require_enum_set(self.event_types, field_name="event_types", member_type=EventType)
        if self.categories is not None:
            _require_enum_set(self.categories, field_name="categories", member_type=EventCategory)
        if self.sources is not None:
            _require_string_set(self.sources, field_name="sources")
        if self.task_ids is not None:
            _require_string_set(self.task_ids, field_name="task_ids")
        if self.correlation_ids is not None:
            _require_uuid_set(self.correlation_ids, field_name="correlation_ids")
        if self.metadata is not None:
            object.__setattr__(
                self,
                "metadata",
                _freeze_predicates(self.metadata, field_name="metadata"),
            )

    def matches(self, event: Event) -> bool:
        """Return whether *event* satisfies every provided constraint exactly.

        The evaluation is a pure structural comparison on canonical fields.
        It interprets nothing, executes nothing, and grants nothing.
        """
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")
        if not self._kind_matches(event):
            return False
        if self.sources is not None and event.source not in self.sources:
            return False
        if self.task_ids is not None and (
            event.task_id is None or event.task_id not in self.task_ids
        ):
            return False
        if self.correlation_ids is not None and event.correlation_id not in self.correlation_ids:
            return False
        if self.metadata is not None:
            for key, expected in self.metadata.items():
                actual = event.metadata.get(key, _MISSING)
                if actual is _MISSING or not _scalar_equal(expected, actual):
                    return False
        return True

    def _kind_matches(self, event: Event) -> bool:
        if self.event_types is None and self.categories is None:
            return True
        return (self.event_types is not None and event.event_type in self.event_types) or (
            self.categories is not None and event.category in self.categories
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible filter representation."""
        metadata: object = (
            None if self.metadata is None else {key: value for key, value in self.metadata.items()}
        )
        return {
            "event_types": (
                None if self.event_types is None else sorted(t.value for t in self.event_types)
            ),
            "categories": (
                None if self.categories is None else sorted(c.value for c in self.categories)
            ),
            "sources": None if self.sources is None else sorted(self.sources),
            "task_ids": None if self.task_ids is None else sorted(self.task_ids),
            "correlation_ids": (
                None
                if self.correlation_ids is None
                else sorted(str(u) for u in self.correlation_ids)
            ),
            "metadata": metadata,
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EventFilter:
        """Validate and deserialize a declarative filter document.

        Only the exact declarative vocabulary is accepted. Unknown fields,
        wrong shapes, duplicate members, non-scalar metadata values, and any
        non-declarative value (including callables) fail closed.
        """
        _require_exact_keys(
            raw,
            required=frozenset(
                {
                    "event_types",
                    "categories",
                    "sources",
                    "task_ids",
                    "correlation_ids",
                    "metadata",
                }
            ),
            context="event filter",
        )
        return cls(
            event_types=_parse_enum_members(
                raw["event_types"], enum_type=EventType, field_name="event_types"
            ),
            categories=_parse_enum_members(
                raw["categories"], enum_type=EventCategory, field_name="categories"
            ),
            sources=_parse_string_members(raw["sources"], field_name="sources"),
            task_ids=_parse_string_members(raw["task_ids"], field_name="task_ids"),
            correlation_ids=_parse_uuid_members(
                raw["correlation_ids"], field_name="correlation_ids"
            ),
            metadata=_parse_predicate_members(raw["metadata"], field_name="metadata"),
        )


def _parse_enum_members[EnumT: (EventType, EventCategory)](
    raw: object,
    *,
    enum_type: type[EnumT],
    field_name: str,
) -> frozenset[EnumT] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise EventWatcherValidationError(f"{field_name} must be a list or null")
    members: list[EnumT] = []
    for item in raw:
        if not isinstance(item, str):
            raise EventWatcherValidationError(f"{field_name} members must be strings")
        try:
            members.append(enum_type(item))
        except ValueError as exc:
            raise EventWatcherValidationError(f"unknown {field_name} member: {item!r}") from exc
    _reject_duplicates(members, field_name=field_name)
    if not members:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use null for no filter")
    if len(members) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    return frozenset(members)


def _parse_string_members(raw: object, *, field_name: str) -> frozenset[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise EventWatcherValidationError(f"{field_name} must be a list or null")
    members: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise EventWatcherValidationError(f"{field_name} members must be strings")
        members.append(_validate_member_text(item, field_name=field_name))
    _reject_duplicates(members, field_name=field_name)
    if not members:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use null for no filter")
    if len(members) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    return frozenset(members)


def _parse_uuid_members(raw: object, *, field_name: str) -> frozenset[UUID] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise EventWatcherValidationError(f"{field_name} must be a list or null")
    members: list[UUID] = []
    for item in raw:
        if not isinstance(item, str):
            raise EventWatcherValidationError(f"{field_name} members must be UUID strings")
        members.append(_parse_uuid_text(item, field_name=field_name))
    _reject_duplicates(members, field_name=field_name)
    if not members:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use null for no filter")
    if len(members) > MAX_FILTER_MEMBERS:
        raise EventWatcherValidationError(
            f"{field_name} must hold at most {MAX_FILTER_MEMBERS} members"
        )
    return frozenset(members)


def _parse_predicate_members(raw: object, *, field_name: str) -> Mapping[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise EventWatcherValidationError(f"{field_name} must be an object or null")
    try:
        frozen = _freeze_predicates(raw, field_name=field_name)
    except TypeError as exc:
        raise EventWatcherValidationError(
            f"{field_name} must contain only bool, int, str, or null scalar values"
        ) from exc
    if not frozen:
        raise EventWatcherValidationError(f"{field_name} must not be empty; use null for no filter")
    return frozen


def _reject_duplicates(members: Iterable[object], *, field_name: str) -> None:
    seen: list[object] = []
    for member in members:
        if member in seen:
            raise EventWatcherValidationError(f"{field_name} must not contain duplicate members")
        seen.append(member)


# ---------------------------------------------------------------------------
# Watcher state (DATA only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EventWatcher:
    """Immutable snapshot of one watcher's identity, filter, and position.

    ``checkpoint`` is the exclusive journal-sequence cursor: every journal
    entry with ``sequence <= checkpoint`` has already been processed by this
    watcher and must never produce a match again. ``last_processed_event_id``
    records the identity of the last processed event when that event was
    read from the journal; a watcher positioned via ``start_checkpoint``
    without reading an event carries ``None``.

    A cancelled watcher is terminal: it can never be re-enabled and never
    observes again.
    """

    watcher_id: WatcherId
    name: str | None
    filter: EventFilter
    created_at: datetime
    enabled: bool = True
    cancelled: bool = False
    checkpoint: int = 0
    last_processed_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId")
        if self.name is not None:
            if not isinstance(self.name, str):
                raise TypeError("name must be a string or None")
            if self.name == "" or self.name != self.name.strip():
                raise EventWatcherValidationError("name must be non-empty and trimmed")
            if len(self.name) > MAX_WATCHER_NAME_LENGTH:
                raise EventWatcherValidationError(
                    f"name must be at most {MAX_WATCHER_NAME_LENGTH} characters"
                )
        if not isinstance(self.filter, EventFilter):
            raise TypeError("filter must be an EventFilter")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        if not isinstance(self.cancelled, bool):
            raise TypeError("cancelled must be a boolean")
        if self.cancelled and self.enabled:
            raise EventWatcherValidationError("a cancelled watcher cannot remain enabled")
        if not isinstance(self.checkpoint, int) or isinstance(self.checkpoint, bool):
            raise TypeError("checkpoint must be an integer")
        if self.checkpoint < 0:
            raise EventWatcherValidationError("checkpoint must be a non-negative journal sequence")
        if self.last_processed_event_id is not None:
            if not isinstance(self.last_processed_event_id, UUID):
                raise TypeError("last_processed_event_id must be a UUID or None")
            if self.last_processed_event_id.int == 0:
                raise EventWatcherValidationError(
                    "last_processed_event_id must not be the nil UUID"
                )
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a timezone-aware datetime")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise EventWatcherValidationError("created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible watcher-state document."""
        return {
            "schema_version": WATCHER_STATE_SCHEMA_VERSION,
            "watcher_id": self.watcher_id.to_str(),
            "name": self.name,
            "filter": self.filter.to_dict(),
            "enabled": self.enabled,
            "cancelled": self.cancelled,
            "checkpoint": self.checkpoint,
            "last_processed_event_id": (
                None if self.last_processed_event_id is None else str(self.last_processed_event_id)
            ),
            "created_at": _format_timestamp(self.created_at),
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EventWatcher:
        """Validate and deserialize a canonical watcher-state document."""
        version = raw.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise EventWatcherValidationError("schema_version must be an integer")
        if version != WATCHER_STATE_SCHEMA_VERSION:
            raise EventWatcherValidationError(
                f"unsupported watcher-state schema version {version}; "
                f"supported version is {WATCHER_STATE_SCHEMA_VERSION}"
            )
        _require_exact_keys(
            raw,
            required=frozenset(
                {
                    "schema_version",
                    "watcher_id",
                    "name",
                    "filter",
                    "enabled",
                    "cancelled",
                    "checkpoint",
                    "last_processed_event_id",
                    "created_at",
                }
            ),
            context="watcher state",
        )

        watcher_id_raw = raw["watcher_id"]
        if not isinstance(watcher_id_raw, str):
            raise EventWatcherValidationError("watcher_id must be a UUID string")

        name = raw["name"]
        if name is not None and not isinstance(name, str):
            raise EventWatcherValidationError("name must be a string or null")

        checkpoint = raw["checkpoint"]
        if not isinstance(checkpoint, int) or isinstance(checkpoint, bool):
            raise EventWatcherValidationError("checkpoint must be an integer")

        last_processed = raw["last_processed_event_id"]
        if last_processed is not None and not isinstance(last_processed, str):
            raise EventWatcherValidationError("last_processed_event_id must be a string or null")

        enabled = raw["enabled"]
        if not isinstance(enabled, bool):
            raise EventWatcherValidationError("enabled must be a boolean")
        cancelled = raw["cancelled"]
        if not isinstance(cancelled, bool):
            raise EventWatcherValidationError("cancelled must be a boolean")

        filter_raw = raw["filter"]
        if not isinstance(filter_raw, Mapping):
            raise EventWatcherValidationError("filter must be an object")

        return cls(
            watcher_id=WatcherId.parse(watcher_id_raw),
            name=name,
            filter=EventFilter.from_dict(filter_raw),
            created_at=_parse_timestamp(raw["created_at"], field_name="created_at"),
            enabled=enabled,
            cancelled=cancelled,
            checkpoint=checkpoint,
            last_processed_event_id=(
                None
                if last_processed is None
                else _parse_uuid_text(last_processed, field_name="last_processed_event_id")
            ),
        )


# ---------------------------------------------------------------------------
# Match result (DATA only)
# ---------------------------------------------------------------------------


class MatchOrigin(StrEnum):
    """Where a match was observed: live bus delivery or journal processing."""

    LIVE = "live"
    JOURNAL = "journal"


@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherMatch:
    """One watcher's match against one canonical Event. A MATCH IS DATA.

    This record performs no action and grants no authority. It deliberately
    carries no permission, authority, risk, budget, or capability field:
    there is nothing a match could authorize.
    """

    watcher_id: WatcherId
    event_id: UUID
    event_type: EventType
    event_timestamp: datetime
    origin: MatchOrigin
    observed_at: datetime
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId")
        if not isinstance(self.event_id, UUID):
            raise TypeError("event_id must be a UUID")
        if not isinstance(self.event_type, EventType):
            raise TypeError("event_type must be an EventType")
        if not isinstance(self.event_timestamp, datetime):
            raise TypeError("event_timestamp must be a datetime")
        if not isinstance(self.origin, MatchOrigin):
            raise TypeError("origin must be a MatchOrigin")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime")
        if self.sequence is not None and (
            not isinstance(self.sequence, int) or isinstance(self.sequence, bool)
        ):
            raise TypeError("sequence must be an integer or None")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible match representation."""
        return {
            "watcher_id": self.watcher_id.to_str(),
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "event_timestamp": _format_timestamp(self.event_timestamp),
            "origin": self.origin.value,
            "observed_at": _format_timestamp(self.observed_at),
            "sequence": self.sequence,
        }


# ---------------------------------------------------------------------------
# Durable watcher-state store
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventWatcherStore:
    """Durable storage for canonical watcher states in SQLite.

    The canonical serialized watcher state is the source representation; the
    ``watcher_id`` column is extracted only for identity, duplicate
    protection, and stable registration ordering.
    """

    database: SQLiteDatabase

    def insert(self, watcher: EventWatcher) -> None:
        """Insert one new watcher state, failing on duplicate identity."""
        self._validate_watcher(watcher)
        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    connection.execute(
                        f"INSERT INTO {_WATCHER_TABLE} (watcher_id, watcher_json) VALUES (?, ?)",
                        (watcher.watcher_id.to_str(), watcher.to_json()),
                    )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    raise DuplicateWatcherIdError(
                        f"Watcher {watcher.watcher_id} already exists in the watcher store"
                    ) from exc
                raise EventWatcherStorageError(
                    f"Unable to insert watcher {watcher.watcher_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise EventWatcherStorageError(
                    f"Unable to insert watcher {watcher.watcher_id}"
                ) from exc

    def update(self, watcher: EventWatcher) -> None:
        """Update one existing watcher state, failing when it is absent."""
        self.update_many((watcher,))

    def update_many(self, watchers: tuple[EventWatcher, ...]) -> None:
        """Atomically update several watcher states in one transaction.

        All updates commit together or not at all; an unknown watcher id
        fails closed and rolls the whole batch back.
        """
        for watcher in watchers:
            self._validate_watcher(watcher)
        if not watchers:
            return
        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    for watcher in watchers:
                        cursor = connection.execute(
                            f"""
                            UPDATE {_WATCHER_TABLE}
                            SET watcher_json = ?,
                                updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE watcher_id = ?
                            """,
                            (watcher.to_json(), watcher.watcher_id.to_str()),
                        )
                        if cursor.rowcount != 1:
                            raise UnknownWatcherIdError(
                                f"Watcher {watcher.watcher_id} is absent from the watcher store"
                            )
            except sqlite3.Error as exc:
                raise EventWatcherStorageError(
                    "Unable to update watcher states in the watcher store"
                ) from exc

    def get(self, watcher_id: WatcherId) -> EventWatcher | None:
        """Return one watcher state by identity, or None when absent."""
        if not isinstance(watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId")
        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"SELECT watcher_id, watcher_json FROM {_WATCHER_TABLE} WHERE watcher_id = ?",
                    (watcher_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise EventWatcherStorageError(
                    f"Unable to read watcher {watcher_id} from the watcher store"
                ) from exc
        if row is None:
            return None
        return _decode_row(row)

    def list_watchers(self) -> tuple[EventWatcher, ...]:
        """Return every stored watcher state in durable registration order."""
        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT watcher_id, watcher_json FROM {_WATCHER_TABLE}
                    ORDER BY registration_sequence ASC
                    """
                ).fetchall()
            except sqlite3.Error as exc:
                raise EventWatcherStorageError("Unable to read the watcher store") from exc
        return tuple(_decode_row(row) for row in rows)

    @staticmethod
    def _validate_watcher(watcher: EventWatcher) -> None:
        if not isinstance(watcher, EventWatcher):
            raise TypeError("watcher must be a canonical EventWatcher")


def _decode_row(row: sqlite3.Row) -> EventWatcher:
    watcher_id_raw = row["watcher_id"]
    watcher_json_raw = row["watcher_json"]
    if not isinstance(watcher_id_raw, str) or not watcher_id_raw:
        raise CorruptWatcherStateError(watcher_id="<invalid>")
    if not isinstance(watcher_json_raw, str) or not watcher_json_raw:
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw)
    try:
        decoded: object = json.loads(watcher_json_raw)
    except json.JSONDecodeError as exc:
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw) from exc
    if not isinstance(decoded, Mapping):
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw)
    if not all(isinstance(key, str) for key in decoded):
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw)
    try:
        watcher = EventWatcher.from_dict(dict(decoded))
    except EventWatcherError as exc:
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw) from exc
    if watcher.watcher_id.to_str() != watcher_id_raw:
        raise CorruptWatcherStateError(watcher_id=watcher_id_raw)
    return watcher


# ---------------------------------------------------------------------------
# Bounded in-memory helpers
# ---------------------------------------------------------------------------


class _RecentEventIds:
    """Bounded FIFO set of recently observed event ids (duplicate guard)."""

    __slots__ = ("_ids", "_members")

    def __init__(self, capacity: int) -> None:
        self._ids: deque[UUID] = deque(maxlen=capacity)
        self._members: set[UUID] = set()

    def add(self, event_id: UUID) -> None:
        if event_id in self._members:
            return
        capacity = self._ids.maxlen
        if capacity is not None and len(self._ids) >= capacity:
            evicted = self._ids.popleft()
            self._members.discard(evicted)
        self._ids.append(event_id)
        self._members.add(event_id)

    def contains(self, event_id: UUID) -> bool:
        return event_id in self._members


@dataclass(slots=True)
class _WatcherRuntime:
    """Mutable in-memory runtime for one registered watcher."""

    state: EventWatcher
    recent_event_ids: _RecentEventIds = field(
        default_factory=lambda: _RecentEventIds(MAX_RECENT_EVENT_IDS)
    )


# ---------------------------------------------------------------------------
# The watcher framework service
# ---------------------------------------------------------------------------


class EventWatcherService:
    """Generic bounded watcher framework over canonical AgentX events.

    The service owns watcher lifecycle, deterministic filtering, live
    observation, and checkpointed journal processing. It executes nothing and
    grants nothing: every outcome is a :class:`WatcherMatch` record.

    The service never subscribes to an EventBus, never publishes, and never
    appends to the journal on its own. A composition root that wants live
    observation wires ``live_handler()`` into the canonical EventBus and owns
    the resulting subscription; watcher-state durability is wired through the
    :class:`EventWatcherStore`.

    Bookkeeping is guarded by a standard-library lock so concurrent bus
    publishers serialize safely; the service itself starts no threads,
    timers, or background work of any kind.
    """

    def __init__(self, *, journal: EventJournal, store: EventWatcherStore) -> None:
        if not isinstance(journal, EventJournal):
            raise TypeError("journal must be a canonical EventJournal")
        if not isinstance(store, EventWatcherStore):
            raise TypeError("store must be a canonical EventWatcherStore")
        self._journal = journal
        self._store = store
        self._lock = Lock()
        self._watchers: dict[WatcherId, _WatcherRuntime] = {}
        self._recent_matches: deque[WatcherMatch] = deque(maxlen=MAX_RETAINED_MATCHES)
        for state in store.list_watchers():
            if state.watcher_id in self._watchers:  # pragma: no cover - unique index guards this
                raise CorruptWatcherStateError(watcher_id=state.watcher_id.to_str())
            self._watchers[state.watcher_id] = _WatcherRuntime(state=state)

    # -- Lifecycle ---------------------------------------------------------

    def create_watcher(
        self,
        event_filter: EventFilter,
        *,
        name: str | None = None,
        watcher_id: WatcherId | None = None,
        enabled: bool = True,
        start_checkpoint: int = 0,
    ) -> EventWatcher:
        """Register and durably persist one new watcher.

        ``start_checkpoint`` positions the watcher at an explicit journal
        sequence without reading an event (for example to skip known
        history); entries at or below it never produce matches for this
        watcher.
        """
        if not isinstance(event_filter, EventFilter):
            raise TypeError("event_filter must be an EventFilter")
        if watcher_id is not None and not isinstance(watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId or None")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        if not isinstance(start_checkpoint, int) or isinstance(start_checkpoint, bool):
            raise TypeError("start_checkpoint must be an integer")
        if start_checkpoint < 0:
            raise EventWatcherValidationError(
                "start_checkpoint must be a non-negative journal sequence"
            )

        identity = WatcherId.create() if watcher_id is None else watcher_id
        state = EventWatcher(
            watcher_id=identity,
            name=name,
            filter=event_filter,
            created_at=datetime.now(UTC),
            enabled=enabled,
            cancelled=False,
            checkpoint=start_checkpoint,
            last_processed_event_id=None,
        )

        with self._lock:
            if len(self._watchers) >= MAX_REGISTERED_WATCHERS:
                raise EventWatcherConflictError(
                    f"at most {MAX_REGISTERED_WATCHERS} watchers may be registered"
                )
            if identity in self._watchers:
                raise EventWatcherConflictError(
                    f"watcher {identity} is already registered in this service"
                )
            self._store.insert(state)
            self._watchers[identity] = _WatcherRuntime(state=state)
        return state

    def get(self, watcher_id: WatcherId) -> EventWatcher | None:
        """Return the current state snapshot of one watcher, or None."""
        if not isinstance(watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId")
        with self._lock:
            runtime = self._watchers.get(watcher_id)
            return None if runtime is None else runtime.state

    def list_watchers(self) -> tuple[EventWatcher, ...]:
        """Return every watcher state in canonical registration order."""
        with self._lock:
            return tuple(runtime.state for runtime in self._watchers.values())

    def enable(self, watcher_id: WatcherId) -> EventWatcher:
        """Enable a disabled watcher (idempotent for already-enabled watchers)."""
        with self._lock:
            runtime = self._require_watcher(watcher_id)
            state = runtime.state
            if state.cancelled:
                raise EventWatcherConflictError(
                    f"watcher {watcher_id} is cancelled; cancellation is terminal"
                )
            if state.enabled:
                return state
            updated = replace(state, enabled=True)
            self._store.update(updated)
            runtime.state = updated
            return updated

    def disable(self, watcher_id: WatcherId) -> EventWatcher:
        """Disable an enabled watcher (idempotent for already-disabled watchers).

        A disabled watcher keeps its checkpoint: on re-enable it resumes from
        the same journal position and deterministically catches up.
        """
        with self._lock:
            runtime = self._require_watcher(watcher_id)
            state = runtime.state
            if state.cancelled:
                raise EventWatcherConflictError(
                    f"watcher {watcher_id} is cancelled; cancellation is terminal"
                )
            if not state.enabled:
                return state
            updated = replace(state, enabled=False)
            self._store.update(updated)
            runtime.state = updated
            return updated

    def cancel(self, watcher_id: WatcherId) -> EventWatcher:
        """Permanently cancel a watcher. Cancellation is terminal and idempotent."""
        with self._lock:
            runtime = self._require_watcher(watcher_id)
            state = runtime.state
            if state.cancelled:
                return state
            updated = replace(state, enabled=False, cancelled=True)
            self._store.update(updated)
            runtime.state = updated
            return updated

    def _require_watcher(self, watcher_id: WatcherId) -> _WatcherRuntime:
        runtime = self._watchers.get(watcher_id)
        if runtime is None:
            raise EventWatcherConflictError(f"watcher {watcher_id} is not registered")
        return runtime

    # -- Live observation --------------------------------------------------

    def observe_live(self, event: Event) -> tuple[WatcherMatch, ...]:
        """Evaluate one live event against every enabled, non-cancelled watcher.

        Live observation never moves a checkpoint. Duplicate live deliveries
        of the same ``event_id`` are suppressed per watcher through the
        bounded FIFO window. Returns the matches in canonical watcher order.
        """
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")
        matches: list[WatcherMatch] = []
        observed_at = datetime.now(UTC)
        with self._lock:
            for runtime in self._watchers.values():
                state = runtime.state
                if state.cancelled or not state.enabled:
                    continue
                if runtime.recent_event_ids.contains(event.event_id):
                    continue
                if state.filter.matches(event):
                    match = WatcherMatch(
                        watcher_id=state.watcher_id,
                        event_id=event.event_id,
                        event_type=event.event_type,
                        event_timestamp=event.timestamp,
                        origin=MatchOrigin.LIVE,
                        observed_at=observed_at,
                        sequence=None,
                    )
                    runtime.recent_event_ids.add(event.event_id)
                    matches.append(match)
                    self._recent_matches.append(match)
        return tuple(matches)

    def live_handler(self) -> EventHandler:
        """Return a canonical EventBus handler adapter for live observation.

        The service does not subscribe itself; the composition root calls
        ``bus.subscribe(service.live_handler())`` and owns the resulting
        subscription. Matches land in the bounded retained log, queryable via
        :meth:`recent_matches`.
        """

        def handler(event: Event) -> None:
            self.observe_live(event)

        return handler

    # -- Checkpointed journal processing ------------------------------------

    def process_pending(self, *, limit: int = DEFAULT_PROCESS_BATCH) -> tuple[WatcherMatch, ...]:
        """Process pending journal entries for every active watcher.

        Reads at most ``limit`` journal entries after the minimum checkpoint
        of the active watchers, in ascending sequence order. Each watcher
        independently skips entries at or below its own checkpoint (so
        replaying for a lagging watcher never duplicates matches for an
        up-to-date one) and skips re-emitting events it already matched live
        in the current process, while always advancing its checkpoint to the
        highest entry it processed. Changed watcher states persist in one
        transaction. Returns matches ordered by sequence, then canonical
        watcher order.
        """
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        if limit < 1 or limit > MAX_PROCESS_BATCH:
            raise EventWatcherValidationError(f"limit must be between 1 and {MAX_PROCESS_BATCH}")

        matches: list[WatcherMatch] = []
        with self._lock:
            active = [
                runtime
                for runtime in self._watchers.values()
                if runtime.state.enabled and not runtime.state.cancelled
            ]
            if not active:
                return ()
            start = min(runtime.state.checkpoint for runtime in active)
            entries = self._journal.read(after_sequence=start, limit=limit)

            changed: dict[WatcherId, EventWatcher] = {}
            observed_at = datetime.now(UTC)
            previous_sequence = start
            for entry in entries:
                if entry.sequence <= previous_sequence:
                    raise EventWatcherStorageError("event journal returned non-ascending sequences")
                previous_sequence = entry.sequence
                for runtime in active:
                    state = runtime.state
                    if entry.sequence <= state.checkpoint:
                        continue
                    already_matched_live = runtime.recent_event_ids.contains(entry.event.event_id)
                    if not already_matched_live and state.filter.matches(entry.event):
                        match = WatcherMatch(
                            watcher_id=state.watcher_id,
                            event_id=entry.event.event_id,
                            event_type=entry.event.event_type,
                            event_timestamp=entry.event.timestamp,
                            origin=MatchOrigin.JOURNAL,
                            observed_at=observed_at,
                            sequence=entry.sequence,
                        )
                        matches.append(match)
                        self._recent_matches.append(match)
                    runtime.recent_event_ids.add(entry.event.event_id)
                    updated = replace(
                        state,
                        checkpoint=entry.sequence,
                        last_processed_event_id=entry.event.event_id,
                    )
                    runtime.state = updated
                    changed[state.watcher_id] = updated

            if changed:
                self._store.update_many(tuple(changed[w] for w in changed))

        return tuple(matches)

    # -- Retained match log -------------------------------------------------

    def recent_matches(self, *, watcher_id: WatcherId | None = None) -> tuple[WatcherMatch, ...]:
        """Return retained matches, oldest first, optionally for one watcher.

        The log is bounded to the most recent :data:`MAX_RETAINED_MATCHES`
        matches across all watchers and is in-memory only: it is an
        observability surface, not durable processing state.
        """
        if watcher_id is not None and not isinstance(watcher_id, WatcherId):
            raise TypeError("watcher_id must be a WatcherId or None")
        with self._lock:
            if watcher_id is None:
                return tuple(self._recent_matches)
            return tuple(match for match in self._recent_matches if match.watcher_id == watcher_id)
