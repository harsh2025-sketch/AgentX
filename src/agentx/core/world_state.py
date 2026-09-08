"""Canonical provider-neutral on-demand world-state snapshot contract (M6.01).

AgentX's world model is intended to be lazy, on-demand, bounded, and
freshness-aware — NOT a continuous full desktop mirror. This module owns the
smallest inert DATA envelope that can hold one bounded point-in-time view of
the world assembled from facts that providers have ALREADY observed elsewhere.

What this module is:

    * :class:`WorldStateDomain` — a small, closed vocabulary of the world
      domains the current AgentX architecture actually observes:
      ``PROCESS`` / ``WINDOW`` (Windows process discovery and UIA tree
      contracts), ``APPLICATION`` (canonical application scoping), ``BROWSER``
      (browser DOM/selection contracts), ``DEVICE`` (device observation
      contracts), ``FILE_CONTEXT`` (canonical artifact records), and
      ``ENVIRONMENT`` (environmental observation contracts). Each member is
      anchored to a landed contract; this vocabulary deliberately adds no
      world ontology of its own.
    * :class:`WorldStateFact` — one observed fact: a closed domain, an inert
      subject identity/reference, an inert fact key, a bounded
      JSON-compatible observed value (deep-frozen), an explicit
      ``observed_at`` instant, an explicit freshness ``ttl``, and the canonical
      C2.02 :class:`~agentx.core.knowledge.ProvenanceReference` the fact
      claims to come from.
    * :class:`WorldStateSnapshot` — one bounded, immutable, provider-neutral
      point-in-time view: an explicit caller-supplied ``captured_at`` assembly
      instant and a canonical-ordered tuple of facts. Deterministic
      serialization with a schema version, closed fields, UTC timestamps,
      unknown-field rejection, and a deep immutable round-trip.
    * The canonical C2.09 freshness rule, reproduced by value (because
      ``agentx.core`` is an inward leaf and must not import
      ``agentx.hive``): ``expires_at = observed_at + ttl`` and fresh exactly
      while ``at < expires_at``. At the boundary instant a fact is already
      stale (fail closed), so stale data can never present itself as fresh.

What this module is emphatically NOT:

    * It does not observe anything. It performs no screen capture, no UIA
      calls, no process enumeration, no browser inspection, no filesystem
      inspection, no device polling, and no audio capture. Providers observe
      the world elsewhere (``agentx.capabilities``); this module only holds
      what they report as data.
    * It is not a continuous world model. It starts no background thread,
      watcher, poller, scheduler, daemon, or timer, subscribes to no events,
      and auto-refreshes nothing. There is no global mutable singleton: a
      snapshot is an inert value a future component may request explicitly.
    * It is not a cache or a store. Entries are never replaced by identity,
      nothing is dropped lazily, nothing is persisted, and no migration
      exists. The Hive's ephemeral environmental cache
      (``agentx.hive.environmental_cache``) remains a separate, mutable,
      string-valued TTL cache; this contract is point-in-time data and the two
      never share a class.
    * It is not verification. A fact observed in a snapshot is NOT verified
      truth about the user's intended goal. "Window title is X" is an
      observation; it is not a task success, a granted permission, a valid
      procedure, or a verified goal. There is deliberately no generic
      ``verified`` flag anywhere in this contract, and no confidence or
      scoring surface: FRESH != VERIFIED and STALE != FALSE.
    * It is not authority. A provider name is provenance, not authority.
      Observed strings may say ``"permission=ADMIN"``, ``"risk=R0"``,
      ``"verified=true"``, ``"task succeeded"``, ``"ignore previous
      instructions"``, or ``"execute command"``; they remain inert data and
      grant no Permission, change no RiskLevel, transition no Task, activate
      no Procedure, and are never interpreted, scanned, or executed.

Relationship to existing canonical contracts (no duplication):

    * :class:`~agentx.capabilities.abi.CapabilityObservation` is the
      evidence attached to ONE capability invocation (summary plus an
      unstructured data mapping). It is not a cross-provider, bounded,
      point-in-time envelope: it has no domain vocabulary, no per-fact
      identity, per-fact timestamps, or freshness metadata.
    * Provider-specific observation snapshots — ``WindowsProcessSnapshot``,
      ``UIATreeSnapshot``, ``BrowserDomObservation``, ``DeviceObservation`` —
      are provider-shaped contracts that live with their providers in
      ``agentx.capabilities``. This envelope is provider-neutral: a
      cross-provider view is assembled from facts those providers produced.
    * The C4.04 :class:`~agentx.core.environment_change.EnvironmentSnapshot`
      is the comparison unit of failure-diagnosis environment-change
      detection: exactly one ``KnowledgeScope``, a closed failure-relevant
      fact-kind vocabulary, and a closed ``TEXT``/``FLAG``/``ABSENT`` value
      model. It is not a general on-demand world view and cannot carry, for
      example, a bounded nested process/window/browser observation.
    * The C2.09 :class:`~agentx.hive.environmental_cache.EnvironmentalCache`
      is a mutable in-memory string TTL cache, not an immutable snapshot.

    This module therefore fills exactly the missing piece — the cross-provider
    inert snapshot envelope — and reuses the canonical
    :class:`~agentx.core.knowledge.ProvenanceReference` for source identity
    instead of inventing a competing provenance, identifier, scope, or error
    type.

Bounds (hard, fail closed):

    * at most 128 facts per snapshot;
    * observed values are bounded JSON: ``None``, ``bool``, ``int`` within a
      63-bit signed range, finite ``float``, ``str`` of at most 4096
      characters, mappings with string keys, and arrays; maximum nesting
      depth 8; canonical JSON encoding of one value at most 65536 bytes;
    * subject and fact key are non-empty trimmed inert text of at most 512
      and 256 characters without control characters; the provenance reference
      is at most 512 characters;
    * no callables, bytes, file handles, COM objects, native pointers, model
      objects, NaN, Infinity, or self-referential structures.

Identity and duplicates:

    A fact's identity is ``(domain, subject, fact_key)``. A snapshot never
    contains two facts with the same identity, whether their values,
    instants, or sources agree or not. Duplicates are rejected at construction
    and on decode: this model does not support ordered observations of one
    fact, and it never silently picks one by insertion order. Facts are
    stored in deterministic canonical order (domain declaration order, then
    subject, then fact key), independent of the order they were supplied in.

Freshness:

    Every fact carries an explicit ``observed_at`` and ``ttl``; the snapshot
    itself carries an explicit caller-supplied ``captured_at``. No clock is
    ever read in this module. A fact is fresh exactly while
    ``at < observed_at + ttl`` (strict, fail closed). Freshness is
    descriptive only: a fresh fact is not verified, and a stale fact is not
    false. Nothing auto-refreshes.

Serialization:

    Deterministic canonical JSON: integer schema version (currently ``1``),
    closed fields with unknown-field rejection, UTC ISO-8601 timestamps with
    a ``Z`` suffix, integer ``ttl_microseconds``, canonical fact order,
    sorted keys, and no object hooks or dynamic decoding. Decoding re-runs
    every construction rule, so a hand-edited payload cannot smuggle
    duplicates, unbounded values, future-dated facts, or unknown fields.

Owner: M6.01. Belongs to ``agentx.core``; imports only the standard library
and the canonical ``agentx.core.knowledge`` provenance hook.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.knowledge import KnowledgeValidationError, ProvenanceReference

__all__ = [
    "CANONICAL_WORLD_STATE_DOMAINS",
    "WORLD_STATE_SNAPSHOT_SCHEMA_VERSION",
    "UnsupportedWorldStateSchemaVersionError",
    "WorldStateDeserializationError",
    "WorldStateDomain",
    "WorldStateError",
    "WorldStateFact",
    "WorldStateSnapshot",
    "WorldStateValidationError",
]

WORLD_STATE_SNAPSHOT_SCHEMA_VERSION: Final[int] = 1

#: Hard cap on the number of facts one snapshot may carry.
_MAX_FACTS_PER_SNAPSHOT: Final[int] = 128
#: Hard cap on the nesting depth of an observed JSON value.
_MAX_VALUE_DEPTH: Final[int] = 8
#: Hard cap on the canonical JSON encoding size of one observed value.
_MAX_VALUE_ENCODED_BYTES: Final[int] = 65_536
#: Hard cap on the length of one string inside an observed JSON value.
_MAX_VALUE_STRING_LENGTH: Final[int] = 4_096
#: Hard cap on the number of items in one array inside an observed value.
_MAX_VALUE_ARRAY_ITEMS: Final[int] = 4_096
#: Observed integers are bounded to a signed 63-bit range.
_MAX_VALUE_INT: Final[int] = (1 << 63) - 1
_MAX_SUBJECT_LENGTH: Final[int] = 512
_MAX_FACT_KEY_LENGTH: Final[int] = 256
_MAX_SOURCE_REFERENCE_LENGTH: Final[int] = 512

_SNAPSHOT_FIELDS: Final[frozenset[str]] = frozenset({"schema_version", "captured_at", "facts"})
_FACT_FIELDS: Final[frozenset[str]] = frozenset(
    {"domain", "subject", "fact_key", "value", "observed_at", "ttl_microseconds", "source"}
)


class WorldStateError(ValueError):
    """Base error for the world-state snapshot contract."""


class WorldStateValidationError(WorldStateError):
    """Raised when world-state data violates the canonical contract."""


class WorldStateDeserializationError(WorldStateError):
    """Raised when encoded world-state data cannot be decoded safely."""


class UnsupportedWorldStateSchemaVersionError(WorldStateDeserializationError):
    """Raised when encoded snapshot data uses a schema version this code cannot read."""


class WorldStateDomain(StrEnum):
    """Small closed vocabulary of the world domains AgentX actually observes.

    Every member is anchored to a landed contract in the current architecture
    and exists so a cross-provider snapshot can name which domain a fact came
    from. A domain names a category of fact; it never asserts that anything
    in that domain is true, available, or verified, and it grants nothing.

    Members:
        PROCESS: Process-state facts. Anchored to the Windows process
            discovery contract (``WindowsProcessSnapshot`` and
            ``WindowsProcessIdentity``).
        WINDOW: Window-state facts. Anchored to the Windows window identity
            and UIA tree contracts (``WindowsWindowIdentity``,
            ``UIATreeSnapshot``).
        APPLICATION: Application-level facts. Anchored to the canonical
            application scoping (``ScopeDimension.APPLICATION``) and
            capability application scoping.
        BROWSER: Browser-state facts. Anchored to the browser observation
            contracts (``BrowserDomObservation``, ``BrowserDomSelectionResult``).
        DEVICE: Device-state facts. Anchored to the device observation
            contract (``DeviceObservation``).
        FILE_CONTEXT: File-context facts. Anchored to the canonical artifact
            record contract (``ArtifactRecord`` with its file/directory/URI
            kinds).
        ENVIRONMENT: Task-relevant environmental facts. Anchored to the
            environmental observation contracts (C2.09 environmental cache
            and C4.04 environment-change facts).

    Deliberately absent: ROBOT, IOT, EMOTION, GESTURE, BIOMETRIC, and any
    other domain the current architecture does not observe. Extending this
    vocabulary is a contract change owned by a later task, never an open
    string.
    """

    PROCESS = "process"
    WINDOW = "window"
    APPLICATION = "application"
    BROWSER = "browser"
    DEVICE = "device"
    FILE_CONTEXT = "file_context"
    ENVIRONMENT = "environment"


#: The canonical domains in declaration order. The declaration order is the
#: canonical fact ordering used by :class:`WorldStateSnapshot`.
CANONICAL_WORLD_STATE_DOMAINS: Final[tuple[WorldStateDomain, ...]] = tuple(WorldStateDomain)


def _validate_inert_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed inert contract text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise WorldStateValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise WorldStateValidationError(f"{field_name} must not exceed {max_length} characters")
    if any(character < " " or character == "\x7f" for character in value):
        raise WorldStateValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    """Validate a timezone-aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorldStateValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise WorldStateDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorldStateDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorldStateDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise WorldStateValidationError("ttl must be strictly positive")
    return value


def _freeze_json(value: object, *, path: str, depth: int) -> object:
    """Validate a bounded JSON-compatible value and deep-freeze it.

    The frozen form is the canonical in-memory representation: mappings
    become ``MappingProxyType`` and arrays become tuples, so no part of the
    value can later be mutated in place. Everything that is not ``None``,
    ``bool``, bounded ``int``, finite ``float``, bounded-length ``str``,
    string-keyed mapping, or array is rejected — callables, bytes, file
    handles, COM objects, native pointers, model objects, NaN, Infinity, and
    self-referential structures all fail closed.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_STRING_LENGTH:
            raise WorldStateValidationError(
                f"{path} contains a string longer than {_MAX_VALUE_STRING_LENGTH} characters"
            )
        return value
    if type(value) is int:
        if value < -_MAX_VALUE_INT - 1 or value > _MAX_VALUE_INT:
            raise WorldStateValidationError(
                f"{path} contains an integer outside the bounded 63-bit range"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldStateValidationError(f"{path} contains a non-finite float")
        return value
    if depth > _MAX_VALUE_DEPTH:
        raise WorldStateValidationError(
            f"{path} exceeds the maximum nesting depth {_MAX_VALUE_DEPTH}"
        )
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorldStateValidationError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}", depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        if len(value) > _MAX_VALUE_ARRAY_ITEMS:
            raise WorldStateValidationError(
                f"{path} contains an array with more than {_MAX_VALUE_ARRAY_ITEMS} items"
            )
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise WorldStateValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_value(value: object, *, path: str) -> object:
    """Convert a frozen JSON value back to plain JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise WorldStateValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise WorldStateValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise WorldStateValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _canonical_json_bytes(value: object) -> int:
    """Return the size of the canonical JSON encoding of one frozen value."""
    return len(
        json.dumps(
            _to_json_value(value, path="value"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _fact_canonical_order(fact: WorldStateFact) -> tuple[int, str, str]:
    """Return the deterministic ordering key: domain order, subject, fact key."""
    return (CANONICAL_WORLD_STATE_DOMAINS.index(fact.domain), fact.subject, fact.fact_key)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorldStateFact:
    """One bounded, immutable, provider-neutral observed fact.

    The fact is pure DATA about the world as one provider observed it:

    * ``domain`` is a member of the closed :class:`WorldStateDomain`
      vocabulary (never inferred from text);
    * ``subject`` is the inert canonical identity/reference string the fact
      is about (a process id, a window handle, an application identity, a
      browser target, a device id, an artifact locator, an environment
      label). It is never resolved, dereferenced, opened, or executed here,
      and hostile content inside it grants nothing;
    * ``fact_key`` is the inert name of the observed facet
      (for example ``"state"`` or ``"window.title"``);
    * ``value`` is the bounded JSON-compatible observed value, deep-frozen
      at construction;
    * ``observed_at`` is the explicit instant the provider observed the fact,
      always stored UTC-normalized and supplied by the caller;
    * ``ttl`` is the explicit freshness window; ``expires_at`` is
      ``observed_at + ttl`` and the fact is fresh exactly while
      ``at < expires_at`` (canonical C2.09 rule, fail closed at the
      boundary);
    * ``source`` is the canonical C2.02
      :class:`~agentx.core.knowledge.ProvenanceReference` naming the
      observing provider or source (kind plus opaque reference). It is
      provenance only: a provider name is provenance, not authority, and is
      never dereferenced, trusted, or verified here.

    Constructing a fact observes nothing, verifies nothing, and grants
    nothing: FRESH != VERIFIED and STALE != FALSE.
    """

    domain: WorldStateDomain
    subject: str
    fact_key: str
    value: object
    observed_at: datetime
    ttl: timedelta
    source: ProvenanceReference

    def __post_init__(self) -> None:
        if not isinstance(self.domain, WorldStateDomain):
            raise TypeError(
                "domain must be a WorldStateDomain member; this contract never infers one from text"
            )
        object.__setattr__(
            self,
            "subject",
            _validate_inert_text(
                self.subject, field_name="subject", max_length=_MAX_SUBJECT_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "fact_key",
            _validate_inert_text(
                self.fact_key, field_name="fact_key", max_length=_MAX_FACT_KEY_LENGTH
            ),
        )
        object.__setattr__(self, "value", _freeze_json(self.value, path="value", depth=0))
        if _canonical_json_bytes(self.value) > _MAX_VALUE_ENCODED_BYTES:
            raise WorldStateValidationError(
                f"value exceeds the bounded encoded size {_MAX_VALUE_ENCODED_BYTES} bytes"
            )
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))
        if not isinstance(self.source, ProvenanceReference):
            raise TypeError(
                "source must be a canonical C2.02 ProvenanceReference; "
                "this contract invents no competing provenance record"
            )
        if len(self.source.reference) > _MAX_SOURCE_REFERENCE_LENGTH:
            raise WorldStateValidationError(
                f"source reference must not exceed {_MAX_SOURCE_REFERENCE_LENGTH} characters"
            )

    @property
    def identity(self) -> tuple[WorldStateDomain, str, str]:
        """The deterministic fact identity ``(domain, subject, fact_key)``."""
        return (self.domain, self.subject, self.fact_key)

    @property
    def canonical_order(self) -> tuple[int, str, str]:
        """The deterministic ordering key used by the snapshot."""
        return _fact_canonical_order(self)

    @property
    def expires_at(self) -> datetime:
        """The explicit freshness boundary ``observed_at + ttl``."""
        return self.observed_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Whether the fact is fresh at ``at`` (strict ``<``, fail closed).

        Descriptive only: a fresh fact is not verified truth, and a stale
        fact is not false.
        """
        moment = _validate_timestamp(at, field_name="at")
        return moment < self.expires_at

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "domain": self.domain.value,
            "subject": self.subject,
            "fact_key": self.fact_key,
            "value": _to_json_value(self.value, path="value"),
            "observed_at": _format_timestamp(self.observed_at),
            "ttl_microseconds": self.ttl // timedelta(microseconds=1),
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WorldStateFact:
        """Validate and reconstruct one fact, failing closed."""
        if not isinstance(raw, Mapping):
            raise WorldStateDeserializationError("fact must be a JSON object")
        _require_exact_fields(raw, record_name="world-state fact", fields=_FACT_FIELDS)
        source_raw = raw["source"]
        if not isinstance(source_raw, Mapping):
            raise WorldStateDeserializationError("fact source must be a JSON object")
        try:
            return cls(
                domain=_parse_domain(raw["domain"], field_name="domain"),
                subject=_validate_inert_text(
                    raw["subject"], field_name="subject", max_length=_MAX_SUBJECT_LENGTH
                ),
                fact_key=_validate_inert_text(
                    raw["fact_key"], field_name="fact_key", max_length=_MAX_FACT_KEY_LENGTH
                ),
                value=raw["value"],
                observed_at=_parse_timestamp(raw["observed_at"], field_name="observed_at"),
                ttl=timedelta(microseconds=_parse_ttl_microseconds(raw["ttl_microseconds"])),
                source=ProvenanceReference.from_dict(source_raw),
            )
        except WorldStateDeserializationError:
            raise
        except (WorldStateValidationError, KnowledgeValidationError, TypeError) as exc:
            raise WorldStateDeserializationError(f"fact is invalid: {exc}") from exc


def _require_exact_fields(
    raw: Mapping[str, object], *, record_name: str, fields: frozenset[str]
) -> None:
    actual = set(raw)
    missing = fields - actual
    unknown = actual - fields
    if missing:
        raise WorldStateDeserializationError(
            f"{record_name} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise WorldStateDeserializationError(
            f"{record_name} contains unknown fields: {sorted(unknown)}"
        )


def _parse_domain(value: object, *, field_name: str) -> WorldStateDomain:
    if not isinstance(value, str):
        raise WorldStateDeserializationError(f"{field_name} must be a string")
    try:
        return WorldStateDomain(value)
    except ValueError as exc:
        raise WorldStateDeserializationError(f"unknown world-state domain: {value!r}") from exc


def _parse_ttl_microseconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorldStateDeserializationError("ttl_microseconds must be an integer")
    if value <= 0:
        raise WorldStateDeserializationError("ttl_microseconds must be strictly positive")
    return value


def _reject_json_constant(name: str) -> float:
    raise WorldStateDeserializationError(
        f"JSON constant {name!r} is not supported; observed values must be bounded and finite"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorldStateSnapshot:
    """One bounded, immutable, provider-neutral point-in-time world view.

    The snapshot is the unit an on-demand component requests and composes.
    It is assembled from facts that providers have ALREADY observed; holding
    a snapshot observes nothing and triggers nothing.

    * ``captured_at`` is the explicit caller-supplied instant at which this
      point-in-time view was assembled. It is never read from a clock in this
      module, and every fact must have been observed at or before it — a
      fact observed after assembly is contradictory and is rejected.
    * ``facts`` is the tuple of :class:`WorldStateFact` in deterministic
      canonical order (domain declaration order, then subject, then fact
      key), independent of the order the facts were supplied in. An empty
      tuple is representable and means "nothing was observed", which is
      explicitly NOT evidence that the world is in any particular state.
    * No two facts may share the identity ``(domain, subject, fact_key)``.
      Duplicates are rejected at construction and on decode: this model does
      not support ordered observations of one fact and never silently picks
      one by insertion order.
    * ``schema_version`` is the integer schema version of the serialized
      form (currently ``1``).

    A snapshot is point-in-time DATA, not a cache or a store: it replaces
    nothing by identity, drops nothing, persists nothing, and auto-refreshes
    nothing. It carries no verification surface: freshness of every fact is
    descriptive (FRESH != VERIFIED, STALE != FALSE), and the snapshot grants
    no authority.
    """

    captured_at: datetime
    facts: tuple[WorldStateFact, ...]
    schema_version: int = WORLD_STATE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != WORLD_STATE_SNAPSHOT_SCHEMA_VERSION:
            raise WorldStateValidationError(
                f"schema_version must be {WORLD_STATE_SNAPSHOT_SCHEMA_VERSION}"
            )
        object.__setattr__(
            self, "captured_at", _validate_timestamp(self.captured_at, field_name="captured_at")
        )
        if not isinstance(self.facts, tuple):
            raise WorldStateValidationError("facts must be a tuple")
        for item in self.facts:
            if not isinstance(item, WorldStateFact):
                raise WorldStateValidationError("facts must contain only WorldStateFact items")
        if len(self.facts) > _MAX_FACTS_PER_SNAPSHOT:
            raise WorldStateValidationError(
                f"snapshot must not carry more than {_MAX_FACTS_PER_SNAPSHOT} facts"
            )
        seen: set[tuple[WorldStateDomain, str, str]] = set()
        for item in self.facts:
            if item.identity in seen:
                raise WorldStateValidationError(
                    "snapshot contains duplicate fact identity "
                    f"{item.identity}; this model does not support ordered "
                    "observations and never picks one by insertion order"
                )
            seen.add(item.identity)
        for item in self.facts:
            if item.observed_at > self.captured_at:
                raise WorldStateValidationError(
                    "a fact observed after the snapshot captured_at instant is "
                    "contradictory; point-in-time views only contain already-"
                    "observed facts"
                )
        object.__setattr__(self, "facts", tuple(sorted(self.facts, key=_fact_canonical_order)))

    @property
    def fact_count(self) -> int:
        """The number of facts this point-in-time view carries."""
        return len(self.facts)

    def is_fresh(self, at: datetime) -> bool:
        """Whether every fact is fresh at ``at`` (strict ``<``, fail closed).

        Descriptive only: an all-fresh snapshot is not verified, and a stale
        fact is not false. A snapshot with no facts has nothing stale.
        """
        moment = _validate_timestamp(at, field_name="at")
        return all(fact.is_fresh(moment) for fact in self.facts)

    def fresh_facts(self, at: datetime) -> tuple[WorldStateFact, ...]:
        """Return the facts fresh at ``at``, in canonical order.

        Pure and descriptive: it filters, never refreshes, and never mutates
        the snapshot.
        """
        moment = _validate_timestamp(at, field_name="at")
        return tuple(fact for fact in self.facts if fact.is_fresh(moment))

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "captured_at": _format_timestamp(self.captured_at),
            "facts": [item.to_dict() for item in self.facts],
        }

    def to_json(self) -> str:
        """Serialize deterministically without object hooks or executable types."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WorldStateSnapshot:
        """Validate and reconstruct one snapshot, failing closed."""
        if not isinstance(raw, Mapping):
            raise WorldStateDeserializationError("snapshot must be a JSON object")
        if "schema_version" not in raw:
            raise WorldStateDeserializationError("snapshot missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise WorldStateDeserializationError("schema_version must be an integer")
        if version != WORLD_STATE_SNAPSHOT_SCHEMA_VERSION:
            raise UnsupportedWorldStateSchemaVersionError(
                f"unsupported world-state schema version {version}; "
                f"supported version is {WORLD_STATE_SNAPSHOT_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw, record_name="world-state snapshot", fields=_SNAPSHOT_FIELDS)
        facts_raw = raw["facts"]
        if not isinstance(facts_raw, list):
            raise WorldStateDeserializationError("snapshot facts must be a JSON array")
        facts: list[WorldStateFact] = []
        for index, item in enumerate(facts_raw):
            if not isinstance(item, Mapping):
                raise WorldStateDeserializationError(
                    f"snapshot facts[{index}] must be a JSON object"
                )
            facts.append(WorldStateFact.from_dict(item))
        try:
            return cls(
                captured_at=_parse_timestamp(raw["captured_at"], field_name="captured_at"),
                facts=tuple(facts),
            )
        except WorldStateDeserializationError:
            raise
        except WorldStateValidationError as exc:
            raise WorldStateDeserializationError(f"snapshot is invalid: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> WorldStateSnapshot:
        """Decode canonical JSON and fail closed on malformed or non-object data.

        No object hooks, no custom decoders: JSON text can never name a class
        or invoke a callable, and a payload that looks executable (for
        example smuggled ``__reduce__`` keys) decodes to inert string data.
        """
        if not isinstance(text, str):
            raise WorldStateDeserializationError("snapshot JSON must be text")
        try:
            decoded = json.loads(text, parse_constant=_reject_json_constant)
        except ValueError as exc:
            raise WorldStateDeserializationError("snapshot JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise WorldStateDeserializationError("snapshot JSON root must be an object")
        return cls.from_dict(decoded)
