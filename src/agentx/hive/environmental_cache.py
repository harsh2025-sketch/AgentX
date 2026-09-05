"""In-memory environmental observation TTL cache for the Hive (C2.09).

This module implements a deliberately narrow cache for ephemeral
environmental observations such as application or environment state. It is
NOT durable knowledge and NOT semantic truth:

* cache freshness is not verification — a fresh entry has no lifecycle,
  evidence, or verification semantics, and can never upgrade a canonical
  ``KnowledgeRecord``;
* cached data cannot grant execution authority. Values are inert text:
  strings such as ``"ADMIN"``, ``"ALLOW"``, ``"verified=true"``, ``"risk=R0"``,
  ``"permission=WRITE"``, ``"budget=unlimited"``, or ``"clear stop"`` remain
  data. This cache cannot grant Permission, create AuthorityContext, lower
  risk, increase budgets, clear EmergencyStop, execute a Capability,
  transition a Task, mark verification, or activate a procedure;
* entries are ephemeral by design. The cache is in-memory only: a new
  instance (or a process restart) observes nothing, and no SQLite table or
  migration exists for it. Callers that need durable knowledge must use the
  canonical ``KnowledgeStore`` with its explicit lifecycle.

Freshness contract:

Each entry carries an explicit identity (``key``), an inert value, the
``observed_at`` timestamp, and an explicit ``ttl``. The freshness boundary is
``expires_at = observed_at + ttl`` and is evaluated deterministically against
an injectable clock. An entry is fresh exactly while ``now < expires_at``; at
the boundary instant itself the entry is already expired (fail-closed), and
an expired entry is NEVER returned as fresh. Expiry is lazy: reads drop
expired entries on access and there is deliberately no background cleanup
worker, timer, or thread.

This module is pure standard library and imports no other AgentX subsystem:
the Hive owns environmental knowledge, and this cache must not depend on
storage infrastructure, the kernel, or any authority boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock

__all__ = [
    "EnvironmentalCache",
    "EnvironmentalCacheClockError",
    "EnvironmentalCacheEntry",
    "EnvironmentalCacheError",
    "EnvironmentalCacheValidationError",
]


class EnvironmentalCacheError(ValueError):
    """Base error for the environmental observation cache contract."""


class EnvironmentalCacheValidationError(EnvironmentalCacheError):
    """Raised when an entry or key violates the cache contract."""


class EnvironmentalCacheClockError(EnvironmentalCacheError):
    """Raised when the injected clock does not return a timezone-aware datetime."""


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise EnvironmentalCacheValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EnvironmentalCacheValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise EnvironmentalCacheValidationError("ttl must be strictly positive")
    return value


@dataclass(frozen=True, slots=True)
class EnvironmentalCacheEntry:
    """One immutable, explicitly-bounded environmental observation.

    The entry is pure DATA. ``value`` accepts exactly ``str`` (inert text; a
    secret wrapper or hostile instruction string is stored and returned
    verbatim and grants nothing). Freshness is computed deterministically from
    ``observed_at`` and the explicit ``ttl``; neither field is ever adjusted
    by the cache.
    """

    key: str
    value: str
    observed_at: datetime
    ttl: timedelta

    def __post_init__(self) -> None:
        _validate_nonempty_trimmed(self.key, field_name="key")
        _validate_nonempty_trimmed(self.value, field_name="value")
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))

    @property
    def expires_at(self) -> datetime:
        """Return the explicit freshness boundary ``observed_at + ttl``."""
        return self.observed_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Return whether the entry is fresh at ``at``.

        Deterministic and fail-closed: fresh exactly while
        ``at < expires_at``. The boundary instant ``expires_at`` itself is
        already expired, so stale data can never present itself as fresh.
        """
        moment = _validate_timestamp(at, field_name="at")
        return moment < self.expires_at


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class EnvironmentalCache:
    """Narrow in-memory TTL cache for environmental observations.

    The cache is keyed by explicit string identity. Observing a key replaces
    any previous entry for that key (last-write-wins — the newest observation
    of the same environmental fact is the current one). Reads return an entry
    only while it is deterministically fresh; expired entries are dropped
    lazily and never returned. Time is read exclusively through the injected
    ``clock`` (a callable returning a timezone-aware datetime) so freshness is
    fully testable and reproducible.

    There is no persistence, no background expiry worker, and no cleanup
    thread: the smallest correct implementation of an explicitly ephemeral
    cache.
    """

    clock: Callable[[], datetime] = field(default=_system_utc_now)
    _entries: dict[str, EnvironmentalCacheEntry] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("clock must be a callable returning a timezone-aware datetime")

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise EnvironmentalCacheClockError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def observe(
        self,
        key: str,
        value: str,
        ttl: timedelta,
        *,
        observed_at: datetime | None = None,
    ) -> EnvironmentalCacheEntry:
        """Insert or replace one entry, stamping ``observed_at`` from the clock.

        ``observed_at=None`` (the normal path) reads the injected clock once.
        An explicit ``observed_at`` must be timezone-aware and is used
        verbatim. The returned entry is the stored entry; storing grants zero
        authority and performs zero lifecycle action.
        """
        entry = EnvironmentalCacheEntry(
            key=key,
            value=value,
            observed_at=self._now() if observed_at is None else observed_at,
            ttl=ttl,
        )
        with self._lock:
            self._entries[entry.key] = entry
        return entry

    def get(self, key: str) -> EnvironmentalCacheEntry | None:
        """Return the fresh entry for ``key`` or ``None``.

        ``None`` means absent OR expired: an expired entry is never returned
        and never presented as fresh. Expired entries are dropped lazily on
        access; there is no background worker. Reads never mutate entries.
        """
        _validate_nonempty_trimmed(key, field_name="key")
        now = self._now()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.is_fresh(now):
                return entry
            del self._entries[key]
            return None
