"""C5.09 bounded lazy world-state observation cache with explicit freshness.

This module implements a deliberately narrow, in-memory cache for ephemeral
*observational world state* (for example a browser target snapshot fact, a
window fact, or an environment fact) keyed by a deterministic structured
identity. It reuses the C2.09 environmental TTL/freshness contract
(``agentx.hive.environmental_cache``) — an explicit freshness boundary
``expires_at = observed_at + ttl``, fail-closed semantics at the boundary
instant, lazy expiry with no background worker, and an injected clock — and
adds C5.09 structured identity, explicit lookup outcomes, explicit
invalidation, and a hard bound on growth. It deliberately does **not** import
the Hive module: the cache lives in the capabilities boundary and stays free
of persistence infrastructure, the kernel, and every authority surface.

What this cache is NOT:

* it stores OBSERVATIONS, not verified execution results. It is not the
  A8.07 reusable verified-result cache: an entry has no lifecycle, evidence,
  or verification semantics and a ``FRESH`` outcome can never mean "the
  action succeeded" or "the world still looks like this";
* cache presence does not prove success, grant permission, make UI state
  live, or authorize action. Values are inert text: strings such as
  ``"ADMIN"``, ``"ALLOW"``, ``"verified=true"``, ``"risk=R0"``,
  ``"permission=WRITE"``, ``"budget=unlimited"``, or ``"clear stop"`` remain
  data. This cache cannot grant a permission, create an authority context,
  lower risk, increase budgets, clear an emergency stop, execute a
  capability, transition a task, produce a verification verdict, or activate
  a procedure;
* entries are ephemeral by design. The cache is in-memory only: a new
  instance (or a process restart) observes nothing, and no SQLite table or
  migration exists for it, exactly as for the C2.09 environmental cache.
  Callers that need durable knowledge must use the canonical KnowledgeStore
  with its explicit lifecycle.

Freshness and outcome contract (C2.09-compatible):

Each entry carries a deterministic identity (``key``: scope, target, kind,
and optional environment identity), an inert ``value`` (an observation value
or an opaque reference), the ``source`` that supplied the observation, the
``observed_at`` timestamp, and an explicit ``ttl``. The freshness boundary is
``expires_at = observed_at + ttl`` and is evaluated deterministically against
the injectable clock. An entry is fresh exactly while it is not invalidated
and ``now < expires_at``; at the boundary instant itself the entry is already
stale (fail-closed), and stale data is NEVER presented as fresh. Expiry is
lazy: reads drop expired entries on access and there is deliberately no
background cleanup worker, timer, or thread.

Reads return an explicit ``WorldStateCacheResult`` outcome instead of a bare
value, so stale state can never silently masquerade as fresh:

* ``MISSING`` — nothing was ever observed for the exact identity (or the
  entry already left the bounded, lazily-expired store);
* ``FRESH`` — an entry exists and is deterministically fresh; it is the only
  outcome that ever exposes an entry;
* ``STALE`` — an entry exists but its freshness boundary has passed; the
  entry is dropped lazily (C2.09 semantics) and its value is withheld;
* ``INVALIDATED`` — the entry was explicitly invalidated; its value is
  withheld until it is re-observed or leaves the bounded store.

Determinism: the canonical cache key is a pure function of the exact identity
components (scope, target, kind, environment). Component injection (quotes,
separators, look-alike concatenations) cannot alias two different identities,
an ``environment`` change yields a different identity and therefore never
serves the old environment's data, and eviction order depends only on the
order of writes and the injected clock — never on read patterns.

Growth is bounded twice: ``max_entries`` bounds the number of entries (on
insert, entries already expired at the incoming observation instant are
swept first, then least-recently-written entries are evicted), and value
length is bounded per entry. The bound always wins over retention.

This module is pure standard library and imports no other AgentX subsystem:
the cache must not depend on storage infrastructure, the kernel, the Hive, or
any authority boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Final

__all__ = [
    "CANONICAL_WORLD_STATE_CACHE_OUTCOMES",
    "DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES",
    "WORLD_STATE_CACHE_SCHEMA_VERSION",
    "WorldStateCache",
    "WorldStateCacheClockError",
    "WorldStateCacheEntry",
    "WorldStateCacheError",
    "WorldStateCacheKey",
    "WorldStateCacheOutcome",
    "WorldStateCacheResult",
    "WorldStateCacheValidationError",
]

WORLD_STATE_CACHE_SCHEMA_VERSION: Final[int] = 1
DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES: Final[int] = 1024
_MAX_IDENTITY_COMPONENT_LENGTH: Final[int] = 512
_MAX_VALUE_LENGTH: Final[int] = 65_536
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class WorldStateCacheError(ValueError):
    """Base error for the world-state observation cache contract."""


class WorldStateCacheValidationError(WorldStateCacheError):
    """Raised when a key, entry, result, or bound violates the cache contract."""


class WorldStateCacheClockError(WorldStateCacheError):
    """Raised when the injected clock does not return a timezone-aware datetime."""


class WorldStateCacheOutcome(StrEnum):
    """Explicit, closed lookup-outcome vocabulary for the world-state cache.

    These are lookup facts only: ``FRESH`` identifies an unexpired,
    un-invalidated observation. It is never authorization, never a success
    proof, never a liveness claim, and never a verification verdict.
    """

    MISSING = "missing"
    FRESH = "fresh"
    STALE = "stale"
    INVALIDATED = "invalidated"


CANONICAL_WORLD_STATE_CACHE_OUTCOMES: Final[tuple[WorldStateCacheOutcome, ...]] = (
    WorldStateCacheOutcome.MISSING,
    WorldStateCacheOutcome.FRESH,
    WorldStateCacheOutcome.STALE,
    WorldStateCacheOutcome.INVALIDATED,
)


def _validate_identity_component(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise WorldStateCacheValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_IDENTITY_COMPONENT_LENGTH:
        raise WorldStateCacheValidationError(
            f"{field_name} must not exceed {_MAX_IDENTITY_COMPONENT_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise WorldStateCacheValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_environment(value: object) -> str | None:
    if value is None:
        return None
    return _validate_identity_component(value, field_name="environment identity")


def _validate_value(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"value must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise WorldStateCacheValidationError("value must be non-empty and trimmed")
    if len(value) > _MAX_VALUE_LENGTH:
        raise WorldStateCacheValidationError(
            f"value must not exceed {_MAX_VALUE_LENGTH} characters"
        )
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorldStateCacheValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise WorldStateCacheValidationError("ttl must be strictly positive")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class WorldStateCacheKey:
    """Deterministic structured identity for one cached world-state observation.

    ``scope`` names the observation family (for example ``"browser.dom"`` or
    ``"os.environment"``), ``target`` identifies the exact observed entity
    (for example a canonical C5.02 browser target identity value or a window
    handle reference), ``kind`` names the observation kind within the scope
    (for example ``"document_version"`` or ``"foreground_state"``), and
    ``environment`` optionally pins the observation to one environment
    identity (for example a C5.01/C5.02 provider/session pairing). All
    components are opaque, validated strings; the cache never parses them.
    """

    scope: str
    target: str
    kind: str
    environment: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_component(self.scope, field_name="scope")
        _validate_identity_component(self.target, field_name="target")
        _validate_identity_component(self.kind, field_name="kind")
        _validate_environment(self.environment)

    @property
    def canonical(self) -> str:
        """Return the deterministic canonical form of this exact identity.

        The canonical form is a pure function of the four identity
        components. JSON string encoding keeps components unambiguous: no
        separator, quoting, or concatenation trick can alias two distinct
        identities, and ``environment=None`` never collides with
        ``environment=""`` (empty environments are themselves rejected).
        """
        return json.dumps(
            [self.scope, self.target, self.kind, self.environment],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible identity snapshot."""
        return {
            "scope": self.scope,
            "target": self.target,
            "kind": self.kind,
            "environment": self.environment,
        }

    def to_json(self) -> str:
        """Serialize deterministically without reconstruction hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class WorldStateCacheEntry:
    """One immutable, explicitly-bounded world-state observation.

    The entry is pure DATA. ``value`` is inert text: it may carry an observed
    value or an opaque reference (for example a document version or artifact
    identity); it is stored and returned verbatim, and a secret wrapper or
    hostile instruction string grants nothing. Freshness is computed
    deterministically from ``observed_at`` and the explicit ``ttl``; neither
    field is ever adjusted by the cache. ``invalidated_at`` records an
    explicit invalidation and permanently removes the entry from freshness.
    """

    key: WorldStateCacheKey
    value: str
    source: str
    observed_at: datetime
    ttl: timedelta
    invalidated_at: datetime | None = None
    schema_version: int = WORLD_STATE_CACHE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.key, WorldStateCacheKey):
            raise TypeError(f"key must be a WorldStateCacheKey, got {type(self.key).__name__}")
        _validate_value(self.value)
        _validate_identity_component(self.source, field_name="source")
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))
        if self.invalidated_at is not None:
            object.__setattr__(
                self,
                "invalidated_at",
                _validate_timestamp(self.invalidated_at, field_name="invalidated_at"),
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != WORLD_STATE_CACHE_SCHEMA_VERSION:
            raise WorldStateCacheValidationError(
                f"unsupported world-state cache schema version {self.schema_version}; "
                f"supported version is {WORLD_STATE_CACHE_SCHEMA_VERSION}"
            )

    @property
    def expires_at(self) -> datetime:
        """Return the explicit freshness boundary ``observed_at + ttl``."""
        return self.observed_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Return whether the entry is fresh at ``at``.

        Deterministic and fail-closed, matching C2.09: fresh exactly while
        the entry is not invalidated and ``at < expires_at``. The boundary
        instant ``expires_at`` itself is already stale, so stale data can
        never present itself as fresh.
        """
        moment = _validate_timestamp(at, field_name="at")
        return self.invalidated_at is None and moment < self.expires_at

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible observation snapshot."""
        return {
            "schema_version": self.schema_version,
            "key": self.key.to_dict(),
            "value": self.value,
            "source": self.source,
            "observed_at": _format_timestamp(self.observed_at),
            "ttl_seconds": self.ttl.total_seconds(),
            "expires_at": _format_timestamp(self.expires_at),
            "invalidated_at": (
                None if self.invalidated_at is None else _format_timestamp(self.invalidated_at)
            ),
        }

    def to_json(self) -> str:
        """Serialize deterministically without reconstruction hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class WorldStateCacheResult:
    """Immutable explicit outcome of one cache lookup.

    ``entry`` is present exactly when ``outcome`` is ``FRESH`` — every other
    outcome structurally withholds observation data, so a stale, invalidated,
    or missing entry can never be read as if it were fresh. The result is a
    lookup fact only: it authorizes nothing and verifies nothing.
    """

    key: WorldStateCacheKey
    outcome: WorldStateCacheOutcome
    entry: WorldStateCacheEntry | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, WorldStateCacheKey):
            raise TypeError(f"key must be a WorldStateCacheKey, got {type(self.key).__name__}")
        if not isinstance(self.outcome, WorldStateCacheOutcome):
            raise TypeError(
                f"outcome must be a WorldStateCacheOutcome, got {type(self.outcome).__name__}"
            )
        if self.outcome is WorldStateCacheOutcome.FRESH:
            if not isinstance(self.entry, WorldStateCacheEntry):
                raise WorldStateCacheValidationError(
                    "a FRESH result must carry the fresh WorldStateCacheEntry"
                )
            if self.entry.key != self.key:
                raise WorldStateCacheValidationError(
                    "a FRESH result entry must belong to the exact queried identity"
                )
            if self.entry.invalidated_at is not None:
                raise WorldStateCacheValidationError(
                    "a FRESH result cannot carry an invalidated entry"
                )
        elif self.entry is not None:
            raise WorldStateCacheValidationError(
                "a non-FRESH result must not carry an entry; "
                "stale, invalidated, and missing observations are never exposed as fresh"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible outcome snapshot."""
        return {
            "key": self.key.to_dict(),
            "outcome": self.outcome.value,
            "entry": None if self.entry is None else self.entry.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize deterministically without reconstruction hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorldStateCache:
    """Bounded, lazy, in-memory cache for world-state observations.

    Observing an identity replaces any previous entry for that exact identity
    (last-write-wins — the newest observation of the same world-state fact is
    the current one) and clears any earlier invalidation. Reads return an
    explicit ``WorldStateCacheResult``; an entry is exposed only while it is
    deterministically fresh. Time is read exclusively through the injected
    ``clock`` (a callable returning a timezone-aware datetime) so freshness
    is fully testable and reproducible; each public method reads the clock at
    most once.

    Growth is hard-bounded by ``max_entries``: inserting a new identity into
    a full cache first sweeps entries already expired at the incoming
    observation instant (C2.09 lazy expiry), then evicts entries in
    least-recently-observed order. Reads and invalidations never change that
    eviction order, so it is a deterministic function of observations and
    the clock.

    There is no persistence, no background expiry worker, and no cleanup
    thread: the smallest correct implementation of an explicitly ephemeral,
    explicitly-bounded observation cache.
    """

    clock: Callable[[], datetime] = field(default=_system_utc_now)
    max_entries: int = DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES
    _entries: dict[str, WorldStateCacheEntry] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("clock must be a callable returning a timezone-aware datetime")
        if not isinstance(self.max_entries, int) or isinstance(self.max_entries, bool):
            raise TypeError("max_entries must be an integer")
        if self.max_entries < 1:
            raise WorldStateCacheValidationError("max_entries must be at least 1")

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise WorldStateCacheClockError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _require_key(self, key: object) -> WorldStateCacheKey:
        if not isinstance(key, WorldStateCacheKey):
            raise TypeError(f"key must be a WorldStateCacheKey, got {type(key).__name__}")
        return key

    def _enforce_capacity(self, *, reference: WorldStateCacheEntry) -> None:
        """Make room for one new identity, with ``self._lock`` already held.

        Deterministic two-phase bound: sweep entries already expired at the
        incoming observation instant, then evict least-recently-written
        entries. The bound always wins over retention.
        """
        if len(self._entries) < self.max_entries:
            return
        expired_keys = [
            canonical
            for canonical, stored in self._entries.items()
            if reference.observed_at >= stored.expires_at
        ]
        for canonical in expired_keys:
            del self._entries[canonical]
        while len(self._entries) >= self.max_entries:
            del self._entries[next(iter(self._entries))]

    def observe(
        self,
        key: WorldStateCacheKey,
        value: str,
        source: str,
        ttl: timedelta,
        *,
        observed_at: datetime | None = None,
    ) -> WorldStateCacheEntry:
        """Insert or replace one entry, stamping ``observed_at`` from the clock.

        ``observed_at=None`` (the normal path) reads the injected clock once.
        An explicit ``observed_at`` must be timezone-aware and is used
        verbatim; capacity expiry is then evaluated at that same instant.
        Re-observing an identity clears any earlier invalidation. The
        returned entry is the stored entry; storing grants zero authority and
        performs zero lifecycle action.
        """
        self._require_key(key)
        entry = WorldStateCacheEntry(
            key=key,
            value=value,
            source=source,
            observed_at=self._now() if observed_at is None else observed_at,
            ttl=ttl,
        )
        canonical = key.canonical
        with self._lock:
            if canonical in self._entries:
                del self._entries[canonical]
            else:
                self._enforce_capacity(reference=entry)
            self._entries[canonical] = entry
        return entry

    def get(self, key: WorldStateCacheKey) -> WorldStateCacheResult:
        """Return the explicit lookup outcome for ``key``.

        ``FRESH`` is the only outcome that carries an entry. ``STALE`` means
        the entry existed but crossed its freshness boundary; it is dropped
        lazily on this access (C2.09 semantics), never exposed, and never
        presented as fresh — a later lookup observes ``MISSING``.
        ``INVALIDATED`` means the entry was explicitly invalidated; it is
        never exposed. Reads never mutate entries and never reorder them.
        """
        checked = self._require_key(key)
        now = self._now()
        with self._lock:
            entry = self._entries.get(checked.canonical)
            if entry is None:
                return WorldStateCacheResult(key=checked, outcome=WorldStateCacheOutcome.MISSING)
            if entry.invalidated_at is not None:
                return WorldStateCacheResult(
                    key=checked, outcome=WorldStateCacheOutcome.INVALIDATED
                )
            if not entry.is_fresh(now):
                del self._entries[checked.canonical]
                return WorldStateCacheResult(key=checked, outcome=WorldStateCacheOutcome.STALE)
            return WorldStateCacheResult(
                key=checked, outcome=WorldStateCacheOutcome.FRESH, entry=entry
            )

    def invalidate(self, key: WorldStateCacheKey) -> bool:
        """Explicitly invalidate the entry for ``key``; return whether it did.

        Only an existing, not-yet-invalidated entry transitions: invalidating
        an unknown or already-invalidated identity changes nothing and
        returns ``False`` (no phantom tombstones are ever created).
        Invalidation stamps ``invalidated_at`` from the injected clock and is
        permanent for that entry: it reads ``INVALIDATED`` until the identity
        is re-observed or the bounded store evicts it, and it takes
        precedence over staleness. Exactly one identity is affected;
        look-alike identities are untouched. Invalidation never changes the
        deterministic least-recently-observed eviction order.
        """
        checked = self._require_key(key)
        now = self._now()
        with self._lock:
            canonical = checked.canonical
            entry = self._entries.get(canonical)
            if entry is None or entry.invalidated_at is not None:
                return False
            self._entries[canonical] = replace(entry, invalidated_at=now)
        return True
