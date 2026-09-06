"""A8.07 verified-result caching: safe L0-style reuse of verified results.

This module is the canonical home for *reusable verified execution results*:
the L0 evidence that a later Router decision may treat as
``RoutingEvidence(verified_reusable_result=True)``. It answers exactly one
question::

    Have we already produced a CANONICALLY VERIFIED result for this exact
    operation, with this exact normalized input, in this exact scope,
    environment, capability/procedure version, and under these exact canonical
    preconditions - and is that result still fresh and un-invalidated?

It is a cache of *results*, not a cache of *world state*.

Not the observational cache
---------------------------

The Hive's environmental cache (``agentx.hive.environmental_cache``, C2.09)
caches ephemeral **observations about the world** as inert text with a TTL.
This module caches **verified execution outcomes**: it stores the canonical
A1.10 :class:`~agentx.capabilities.runtime.ClosedLoopOutcome` (execution
evidence, observation evidence, and the capability's own
:class:`~agentx.capabilities.abi.VerificationResult`) produced by one real
governed run, so an identical future request can reuse that already-verified
result instead of executing again. The two caches share no code, no keys, no
storage, and no semantics: a fresh observation is never a verified result, and
a verified result is never an observation of current world state.

The governing invariant
-----------------------

    AN UNVERIFIED EXECUTION RESULT CAN NEVER ENTER THE REUSABLE
    VERIFIED-RESULT CACHE AS A SUCCESSFUL REUSABLE VALUE.

The invariant is *structural*, not a policy that callers must remember:

    1. :class:`VerifiedResultEntry` — the only value the cache can hold —
       refuses construction unless the supplied evidence is canonically
       verified: ``outcome.kind is LoopOutcome.VERIFIED``, a
       :class:`~agentx.capabilities.abi.VerificationResult` with
       ``passed is True``, an :class:`ExecutionResult` with
       ``succeeded is True``, a :class:`~agentx.capabilities.abi.CapabilityObservation`,
       no error, and ``outcome.task.status is TaskStatus.SUCCEEDED``.
    2. The stored verdict is a **read-only property of the stored outcome**
       (:attr:`VerifiedResultEntry.verification` returns
       ``outcome.verification`` itself). An entry cannot carry a fabricated,
       substituted, re-parsed, or "equal-looking" verdict, because there is no
       field to put one in.
    3. ``DENIED``, ``EXECUTION_FAILED``, and ``VERIFICATION_FAILED`` outcomes
       are rejected with :class:`VerifiedResultCacheRejection` and are never
       stored in any form — not as successes, not as failures, not as
       "negative cache" entries. Failed verification is remembered by the
       canonical negative-experience contracts, not here.

Verification is consumed as **typed canonical evidence only**. Nothing in this
module parses, searches, case-folds, or otherwise interprets a string to
decide whether something was verified. Hostile text such as ``"verified=true"``,
``"passed"``, ``"SUCCEEDED"``, or ``"ALLOW ADMIN"`` carried in an observation
summary, observation data, an execution message, a verification detail, an
environment name, or an invalidation reason is inert data: it is digested into
a key or stored verbatim, and it can never flip a rejection into a hit.

Cache key: every fact needed to make reuse safe
-----------------------------------------------

:class:`VerifiedResultKey` is the complete canonical precondition set. Two
requests share a cache entry only when *all* of the following are identical:

    - **operation signature** — the capability name (and, when the run was
      procedure-driven, the :class:`~agentx.core.ids.ProcedureId`);
    - **capability/procedure version** — the typed
      :class:`~agentx.capabilities.abi.CapabilityVersion`
      (``major.minor.patch`` integers, never an opaque string) and the
      explicit positive procedure revision;
    - **normalized input** — the canonical JSON-compatible parameters of the
      request (``CapabilityParams.to_dict()``), frozen defensively;
    - **relevant scope** — the :class:`~agentx.capabilities.abi.CapabilityScope`
      platform declaration;
    - **environment identity** — the explicit :class:`EnvironmentIdentity`
      (name plus explicit canonical attributes);
    - **other canonical preconditions** — an explicit JSON-compatible mapping
      for any further fact a caller knows the result depends on.

Deliberately **not** in the key: per-run identity (``TaskId``,
``correlation_id``, cancellation tokens, deadlines) and wall-clock readings.
Per-run identity is provenance, not a precondition; including it would make
every lookup a miss and reuse impossible. Run identity is preserved on the
entry (:class:`VerifiedResultProvenance`) instead, and the
:class:`~agentx.core.execution.ExecutionContext` is consumed for provenance and
for a task-identity consistency check only — its cancellation token and
deadline are execution-scoped control state and are never stored, never keyed,
and never reused.

Deterministic keying
--------------------

Keys are digested with SHA-256 over **canonical JSON text** (sorted object
keys, compact separators, ASCII escapes, explicit schema/domain tags), so the
digest is a pure function of the typed key material:

    - no ``hash()`` of ``str``/``bytes`` (PYTHONHASHSEED-randomized);
    - no ``id()``, no object identity, no insertion order;
    - no clock reading, no randomness, no UUID, no filesystem or network input;
    - no floating-point arithmetic in key construction (JSON floats are
      serialized by their shortest round-trip representation).

The same key material therefore yields the same fingerprint in every process,
every run, and every dictionary order. ``__hash__`` on the cache's value
objects is derived from that digest, so even in-process container placement is
deterministic. Digests are *identity*, not security: they are always compared
against stored canonical key material, never trusted on their own.

Lookup vocabulary
-----------------

:class:`LookupStatus` distinguishes exactly the five outcomes a caller must
not conflate:

    ``MISS``          nothing is recorded for this operation and input;
    ``VERIFIED_HIT``  a fresh, un-invalidated, canonically verified result
                      exists and may be reused;
    ``STALE``         the recorded verified result exists but its explicit
                      freshness boundary has been reached or passed;
    ``INVALIDATED``   the recorded verified result was explicitly invalidated
                      and can never be reused again. The same status is
                      reported for the pathological case of stored evidence
                      that no longer reads as canonically verified (in-process
                      tampering), which is likewise never reusable;
    ``INCOMPATIBLE``  the same operation and normalized input is recorded, but
                      only under a different scope, environment, capability or
                      procedure version, or different canonical preconditions.

``INCOMPATIBLE`` is deliberately not ``MISS``: a previous verification of the
same operation elsewhere is *evidence that reuse here would be unsafe*, and a
caller must be able to tell "we have never done this" apart from "we did
something like this under different canonical facts". Only ``VERIFIED_HIT``
exposes :attr:`VerifiedResultLookup.reusable_outcome`; every other status
returns ``None``, so a stale or invalidated entry can be inspected for
diagnostics but can never be consumed as a reusable result.

Freshness, expiry, and invalidation
-----------------------------------

Previous verification is **not** proof that the current environment is
unchanged. Reuse is therefore bounded twice, explicitly:

    - **freshness** — every entry carries ``created_at`` and a strictly
      positive ``ttl`` bounded by :data:`MAX_REUSE_TTL`; the boundary is
      ``expires_at = created_at + ttl`` and freshness is ``now < expires_at``
      (the boundary instant itself is already stale — fail closed). Time is
      read only through the injected ``clock``.
    - **invalidation** — :meth:`VerifiedResultCache.invalidate` and
      :meth:`VerifiedResultCache.invalidate_environment` move entries to a
      terminal :attr:`EntryState.INVALIDATED` state carrying an explicit typed
      reason, cause, and timestamp. Invalidation is monotonic for that entry:
      it is never reactivated, and evidence created at or before the recorded
      invalidation can never be re-cached under the same key. A genuinely new
      verified run (newer ``created_at``, new provenance) may be stored, which
      creates a *new* entry rather than resurrecting the old one.

There is no decay, no scoring, no ranking, no usage counter, and no eviction
policy here: retention/trust decay is A8.08 and is not implemented. Capacity is
a bounded-memory safety valve only — when :data:`DEFAULT_MAX_ENTRIES` (or the
explicit ``max_entries``) is reached, entries whose freshness has already
expired are dropped first and a further store is *refused*; nothing is scored
and nothing is thrown away to make room for a "more valuable" entry.

Storage and restart semantics
-----------------------------

The cache is in-memory only, guarded by one lock, with lazy expiry and no
background worker, timer, or thread. A new instance — and therefore a process
restart — observes nothing and returns ``MISS``. That is the safe direction:
verification evidence is never silently inherited across restarts. There is no
SQLite table, no migration, and no file I/O in this module. Durable reuse would
require a Hive/infrastructure-owned store task that re-validates every loaded
entry through the same :class:`VerifiedResultEntry` gate; nothing here
prejudices that design.

No authority, no execution, no routing
--------------------------------------

The cache executes nothing and authorizes nothing. It never calls
``Capability.execute`` or ``Capability.verify``, never resolves a registry
entry, never constructs a :class:`~agentx.capabilities.abi.VerificationResult`,
never rewrites a :class:`~agentx.capabilities.runtime.ClosedLoopOutcome`, never
transitions a :class:`~agentx.core.tasks.Task`, never creates a Permission or
AuthorityContext, never lowers risk, never enlarges a budget, never clears an
emergency stop, and never publishes events or audit records. It imports
``agentx.kernel`` not at all.

A ``VERIFIED_HIT`` is *evidence for a routing decision*, never the decision:
this module does not choose an execution level, does not import or modify the
A2.07 Router, and does not hand anything to it. Whether a hit is used — and
under what authority the reused operation is re-presented — belongs to the
governed runtime path.

Deliberate non-scope
--------------------

Not the C2.09 environmental observation cache, not the A8.08 decay/retention
model, not a Router (A2.07), Executor (A2.04), Verifier (A2.05), Task Manager,
agent loop, negative-experience store, semantic memory, or knowledge store. No
capability execution, no model call, no reasoning, no repair, no research, no
similarity/fuzzy/embedding matching, no wildcard or prefix keys, no
serialization format, no persistence, no event or audit publication, and no
background maintenance.

Owner: A8.07. Belongs to ``agentx.capabilities`` — the canonical subsystem that
owns the outcome contracts it stores — so it adds no top-level package and
widens no boundary edge. It imports only the standard library, canonical
``agentx.core`` contracts, and the canonical sibling A1.08/A1.10 modules.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from types import MappingProxyType
from typing import Any, Final, cast
from uuid import UUID

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityObservation,
    CapabilityRequest,
    CapabilityScope,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.execution import ExecutionContext
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.tasks import JsonValue, Task, TaskStatus

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "KEY_DIGEST_ALGORITHM",
    "MAX_REUSE_TTL",
    "VERIFIED_RESULT_CACHE_SCHEMA_VERSION",
    "VERIFIED_RESULT_CACHE_SOURCE",
    "EntryState",
    "EnvironmentIdentity",
    "InvalidationCause",
    "InvalidationRecord",
    "LookupStatus",
    "ProcedureRevision",
    "VerifiedResultCache",
    "VerifiedResultCacheCapacityError",
    "VerifiedResultCacheClockError",
    "VerifiedResultCacheError",
    "VerifiedResultCacheRejection",
    "VerifiedResultCacheValidationError",
    "VerifiedResultEntry",
    "VerifiedResultKey",
    "VerifiedResultLookup",
    "VerifiedResultProvenance",
    "VerifiedResultSubmission",
]

#: Schema tag included in every digest. Changing the canonical key encoding
#: changes this value, which invalidates every previously computed fingerprint
#: by construction rather than by migration.
VERIFIED_RESULT_CACHE_SCHEMA_VERSION: Final[int] = 1

#: Digest algorithm used for canonical key fingerprints.
KEY_DIGEST_ALGORITHM: Final[str] = "sha256"

#: Default ``source`` recorded in the provenance of stored entries.
VERIFIED_RESULT_CACHE_SOURCE: Final[str] = "agentx.capabilities.verified_result_cache"

#: Upper bound on one reuse window. A verified result is evidence about a past
#: run, never proof that the environment is unchanged forever, so an unbounded
#: TTL is rejected rather than merely discouraged.
MAX_REUSE_TTL: Final[timedelta] = timedelta(days=7)

#: Default bound on the number of simultaneously stored entries. This is a
#: bounded-memory safety valve, not a retention/decay policy (A8.08).
DEFAULT_MAX_ENTRIES: Final[int] = 512

_KEY_DOMAIN: Final[str] = "agentx.capabilities.verified_result_cache.key"
_OPERATION_DOMAIN: Final[str] = "agentx.capabilities.verified_result_cache.operation"

_MAX_NAME_LENGTH: Final[int] = 128
_MAX_TEXT_LENGTH: Final[int] = 512
_MAX_REASON_LENGTH: Final[int] = 256
_MAX_MAPPING_ENTRIES: Final[int] = 64
_MAX_SEQUENCE_ITEMS: Final[int] = 256
_MAX_JSON_DEPTH: Final[int] = 32
_DIGEST_PREFIX_LENGTH: Final[int] = 15
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class VerifiedResultCacheError(ValueError):
    """Base error for the A8.07 verified-result cache contract."""


class VerifiedResultCacheValidationError(VerifiedResultCacheError):
    """Raised when key material, provenance, or request shape is malformed."""


class VerifiedResultCacheRejection(VerifiedResultCacheError):  # noqa: N818
    """Raised when evidence may not be stored or replaced.

    A rejection is never a verdict about the world: it says only that the
    supplied evidence is not a canonically verified reusable result (or would
    replay evidence older than what the cache already recorded). Rejections
    leave the cache unchanged.
    """


class VerifiedResultCacheCapacityError(VerifiedResultCacheError):
    """Raised when a store is refused because the bounded cache is full.

    Refusing to cache is the fail-safe direction: the operation can still be
    executed and verified normally, it simply is not remembered. This is not an
    eviction, decay, or retention decision (A8.08).
    """


class VerifiedResultCacheClockError(VerifiedResultCacheError):
    """Raised when the injected clock does not return a timezone-aware datetime."""


# ---------------------------------------------------------------------------
# Validation helpers.
# ---------------------------------------------------------------------------


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, bounded, control-character-free inert text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise VerifiedResultCacheValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise VerifiedResultCacheValidationError(
            f"{field_name} must not contain control characters"
        )
    if len(value) > max_length:
        raise VerifiedResultCacheValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    """Normalize a required timezone-aware timestamp to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise VerifiedResultCacheValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    """Validate an explicit, strictly positive, bounded reuse window."""
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise VerifiedResultCacheValidationError("ttl must be strictly positive")
    if value > MAX_REUSE_TTL:
        raise VerifiedResultCacheValidationError(
            f"ttl must not exceed MAX_REUSE_TTL ({MAX_REUSE_TTL}); previous verification is "
            "not proof that the current environment is unchanged"
        )
    return value


def _validate_counter(value: object, *, field_name: str) -> int:
    """Validate an explicit positive integer (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise VerifiedResultCacheValidationError(f"{field_name} must be positive")
    return value


def _freeze_json_value(value: object, *, path: str, depth: int = 0) -> object:
    """Validate JSON compatibility and return an immutable defensive copy.

    Mirrors the canonical JSON-value rules of the A1.08 observation contract
    and the A2.05 requirement contract: ``None``, bool, int, finite float, str,
    mappings with string keys, and lists/tuples. Callables, modules, and
    arbitrary objects are rejected, so key material can never smuggle
    executable behaviour. Nesting depth, mapping width, and sequence length are
    bounded so canonicalization and digestion stay deterministic and cheap for
    hostile input.
    """
    if depth > _MAX_JSON_DEPTH:
        raise VerifiedResultCacheValidationError(
            f"{path} exceeds the maximum nesting depth of {_MAX_JSON_DEPTH}"
        )
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise VerifiedResultCacheValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_MAPPING_ENTRIES:
            raise VerifiedResultCacheValidationError(
                f"{path} must not exceed {_MAX_MAPPING_ENTRIES} entries"
            )
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise VerifiedResultCacheValidationError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json_value(item, path=f"{path}.{key}", depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        if len(value) > _MAX_SEQUENCE_ITEMS:
            raise VerifiedResultCacheValidationError(
                f"{path} must not exceed {_MAX_SEQUENCE_ITEMS} items"
            )
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise VerifiedResultCacheValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_json_mapping(value: object, *, path: str) -> Mapping[str, JsonValue]:
    """Validate a JSON object mapping and return its frozen immutable copy."""
    if not isinstance(value, Mapping):
        raise TypeError(
            f"{path} must be a mapping of string keys to JSON-compatible values, "
            f"got {type(value).__name__}"
        )
    frozen = _freeze_json_value(value, path=path)
    return cast("Mapping[str, JsonValue]", frozen)


def _to_jsonable(value: object, *, path: str) -> object:
    """Convert frozen internal JSON values back into plain serializable ones.

    ``json`` cannot serialize :class:`types.MappingProxyType`, and frozen
    sequences are tuples, so canonicalization converts both back to plain
    containers. Ordering is irrelevant here: :func:`_canonical_json` sorts
    object keys, which is what makes the encoding deterministic.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; freezing already rejected this
            raise VerifiedResultCacheValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; freezing already rejected this
                raise VerifiedResultCacheValidationError(f"{path} contains a non-string object key")
            converted[key] = _to_jsonable(item, path=f"{path}.{key}")
        return converted
    if isinstance(value, list | tuple):
        return [_to_jsonable(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise VerifiedResultCacheValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Return the deterministic canonical JSON text of one key payload."""
    serializable = _to_jsonable(payload, path="key")
    if not isinstance(serializable, dict):  # pragma: no cover - payload is always a mapping
        raise AssertionError("canonical key payload did not convert to a JSON object")
    return json.dumps(serializable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(canonical_text: str) -> str:
    """Return the stable SHA-256 hex digest of canonical key text."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _digest_hash(canonical_text: str) -> int:
    """Return a process-stable ``__hash__`` derived from a canonical digest.

    Python's built-in ``hash()`` of ``str`` is randomized per process, so it is
    never used for cache identity. These value objects hash by digest prefix
    instead, which keeps even in-process container placement deterministic.

    Fifteen hex characters (60 bits) are used rather than sixteen so that the
    value is always inside the range ``__hash__`` may return. CPython reduces
    anything larger modulo ``2**61 - 1``, which would make ``hash(value)`` differ
    from the published digest prefix and hide the derivation from callers.
    """
    return int(_digest(canonical_text)[:_DIGEST_PREFIX_LENGTH], 16)


def _verified_evidence_failures(
    outcome: ClosedLoopOutcome,
    verification: object,
) -> tuple[str, ...]:
    """Return every reason this evidence is not a canonically verified result.

    Reads typed canonical fields only — the :class:`LoopOutcome` enum, the
    :class:`VerificationResult` bool, the :class:`TaskStatus` enum, and the
    presence/absence of canonical evidence objects. No string is parsed,
    searched, or case-folded, so hostile text in a summary, message, or detail
    can never satisfy a check. Messages name facts and controlled enum values;
    they never echo evidence content.
    """
    failures: list[str] = []

    kind: object = outcome.kind
    if kind is not LoopOutcome.VERIFIED:
        if isinstance(kind, LoopOutcome):
            failures.append(
                f"canonical outcome kind is {kind.value!r}, not {LoopOutcome.VERIFIED.value!r}"
            )
        else:
            failures.append("canonical outcome kind has an unexpected type")

    if verification is None:
        failures.append("canonical verification evidence is missing")
    elif not isinstance(verification, VerificationResult):
        failures.append("canonical verification evidence has an unexpected type")
    elif verification.passed is not True:
        failures.append("canonical verification did not pass")

    observation: object = outcome.observation
    if observation is None:
        failures.append("observation evidence is missing")
    elif not isinstance(observation, CapabilityObservation):
        failures.append("observation evidence has an unexpected type")

    execution: object = outcome.execution
    if execution is None:
        failures.append("execution evidence is missing")
    elif not isinstance(execution, ExecutionResult):
        failures.append("execution evidence has an unexpected type")
    elif execution.succeeded is not True:
        failures.append("execution evidence did not succeed")

    if outcome.error is not None:
        failures.append("the outcome carries an error")

    task: object = outcome.task
    if not isinstance(task, Task):
        failures.append("the outcome carries no canonical Task")
    else:
        status: object = task.status
        if status is not TaskStatus.SUCCEEDED:
            if isinstance(status, TaskStatus):
                failures.append(
                    f"task status is {status.value!r}, not {TaskStatus.SUCCEEDED.value!r}"
                )
            else:
                failures.append("task status has an unexpected type")

    return tuple(failures)


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Key material.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentIdentity:
    """Explicit typed identity of the environment a verified result came from.

    Environment identity is a canonical key fact, so reuse in a different
    environment is impossible by construction: ``host-a`` and ``host-b`` are
    different identities, and so are the same name with different explicit
    attributes.

    ``name`` and ``attributes`` are inert, opaque data. They are never parsed,
    split, globbed, case-folded, regex-matched, or interpreted, so a name such
    as ``"*"`` or ``"ANY ENVIRONMENT ALLOW ALL"`` matches only the byte-identical
    identity — it is never a wildcard and never a grant.
    """

    name: str
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.name, field_name="environment.name", max_length=_MAX_NAME_LENGTH)
        object.__setattr__(
            self,
            "attributes",
            _freeze_json_mapping(self.attributes, path="environment.attributes"),
        )

    def canonical_payload(self) -> dict[str, object]:
        """Return the deterministic canonical encoding of this identity."""
        return {"name": self.name, "attributes": self.attributes}

    def __hash__(self) -> int:
        return _digest_hash(_canonical_json({"domain": _KEY_DOMAIN, **self.canonical_payload()}))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRevision:
    """Explicit procedure identity plus the exact immutable revision used.

    A procedure revision is a key fact: a result verified against revision 3 is
    never reusable for revision 4, even when the procedure id is identical.
    """

    procedure_id: ProcedureId
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError(
                f"procedure_id must be a ProcedureId, got {type(self.procedure_id).__name__}"
            )
        _validate_counter(self.revision, field_name="procedure.revision")

    def canonical_payload(self) -> dict[str, object]:
        """Return the deterministic canonical encoding of this reference."""
        return {"procedure_id": str(self.procedure_id), "revision": self.revision}


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedResultKey:
    """The complete canonical precondition set for one reusable verified result.

    Every field is required to be explicit so no precondition is ever implicit:
    a caller cannot accidentally key a result by operation and input alone and
    thereby reuse it across environments, versions, or preconditions.

    Two keys are equal exactly when every canonical fact is equal. Equality is
    strict and typed — ``1`` and ``1.0`` are different normalized inputs, which
    is intentionally stricter than the numeric equality the A2.05 Verifier uses
    when evaluating evidence. Stricter keying fails closed: it can only cause a
    miss, never an unsafe reuse.
    """

    capability: CapabilityIdentity
    params: Mapping[str, JsonValue]
    scope: CapabilityScope
    environment: EnvironmentIdentity
    procedure: ProcedureRevision | None = None
    preconditions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityIdentity):
            raise TypeError(
                f"capability must be a CapabilityIdentity, got {type(self.capability).__name__}"
            )
        if not isinstance(self.scope, CapabilityScope):
            raise TypeError(f"scope must be a CapabilityScope, got {type(self.scope).__name__}")
        if not isinstance(self.environment, EnvironmentIdentity):
            raise TypeError(
                f"environment must be an EnvironmentIdentity, got {type(self.environment).__name__}"
            )
        if self.procedure is not None and not isinstance(self.procedure, ProcedureRevision):
            raise TypeError(
                "procedure must be a ProcedureRevision or None, "
                f"got {type(self.procedure).__name__}"
            )
        object.__setattr__(self, "params", _freeze_json_mapping(self.params, path="params"))
        object.__setattr__(
            self,
            "preconditions",
            _freeze_json_mapping(self.preconditions, path="preconditions"),
        )

    @classmethod
    def from_request(
        cls,
        request: CapabilityRequest[Any],
        *,
        scope: CapabilityScope,
        environment: EnvironmentIdentity,
        procedure: ProcedureRevision | None = None,
        preconditions: Mapping[str, JsonValue] | None = None,
    ) -> VerifiedResultKey:
        """Build canonical key material from an inert A1.08 request.

        The normalized input is the request's own typed
        ``CapabilityParams.to_dict()`` — never a ``repr()``, never a raw dict
        passed by a caller, and never model-generated text. Building a key does
        not execute, resolve, or authorize anything.
        """
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        return cls(
            capability=request.identity,
            params=request.params.to_dict(),
            scope=scope,
            environment=environment,
            procedure=procedure,
            preconditions={} if preconditions is None else preconditions,
        )

    def canonical_text(self) -> str:
        """Return the deterministic canonical JSON text of the full key."""
        return _canonical_json(self.canonical_payload())

    def canonical_payload(self) -> dict[str, object]:
        """Return every canonical fact that must match for safe reuse."""
        procedure = self.procedure
        return {
            "domain": _KEY_DOMAIN,
            "schema": VERIFIED_RESULT_CACHE_SCHEMA_VERSION,
            "capability": {
                "name": str(self.capability.name),
                "version": [
                    self.capability.version.major,
                    self.capability.version.minor,
                    self.capability.version.patch,
                ],
            },
            "params": self.params,
            "scope": {"platform": self.scope.platform.value},
            "environment": self.environment.canonical_payload(),
            "procedure": None if procedure is None else procedure.canonical_payload(),
            "preconditions": self.preconditions,
        }

    def fingerprint(self) -> str:
        """Return the stable SHA-256 fingerprint of the complete key.

        The fingerprint is a pure function of typed key material: no clock, no
        randomness, no ``hash()``, no object identity, and no dictionary
        ordering can change it.
        """
        return _digest(self.canonical_text())

    def operation_payload(self) -> dict[str, object]:
        """Return the weaker operation-plus-input identity.

        This identity is used *only* to classify a lookup as ``INCOMPATIBLE``
        when the same operation and normalized input are recorded under
        different canonical facts. It can never authorize reuse: no entry is
        ever returned on the strength of an operation fingerprint alone.
        """
        procedure = self.procedure
        return {
            "domain": _OPERATION_DOMAIN,
            "schema": VERIFIED_RESULT_CACHE_SCHEMA_VERSION,
            "capability_name": str(self.capability.name),
            "procedure_id": None if procedure is None else str(procedure.procedure_id),
            "params": self.params,
        }

    def operation_fingerprint(self) -> str:
        """Return the stable fingerprint of the operation-plus-input identity."""
        return _digest(_canonical_json(self.operation_payload()))

    def __hash__(self) -> int:
        return _digest_hash(self.canonical_text())


#: Canonical facts compared, in deterministic order, to explain why a recorded
#: verified result cannot be reused for a requested key. Names only: the
#: values themselves are never echoed, so hostile text cannot leak through a
#: lookup result.
_CANONICAL_FACTS: Final[tuple[str, ...]] = (
    "capability_version",
    "environment",
    "preconditions",
    "procedure_revision",
    "scope",
)


def _fact_differences(requested: VerifiedResultKey, stored: VerifiedResultKey) -> tuple[str, ...]:
    """Return the canonical facts that differ between two keys."""
    differences: list[str] = []
    if requested.capability.version != stored.capability.version:
        differences.append("capability_version")
    if requested.environment != stored.environment:
        differences.append("environment")
    if requested.preconditions != stored.preconditions:
        differences.append("preconditions")
    if requested.procedure != stored.procedure:
        differences.append("procedure_revision")
    if requested.scope != stored.scope:
        differences.append("scope")
    if not differences:
        # Only reachable on a digest collision: never silently claim reuse.
        differences.append("canonical_key")
    return tuple(sorted(differences))


def _fact_order(fact: str) -> tuple[int, str]:
    """Return the deterministic sort position of one canonical fact name."""
    position = _CANONICAL_FACTS.index(fact) if fact in _CANONICAL_FACTS else len(_CANONICAL_FACTS)
    return (position, fact)


def _incompatibility_reasons(
    requested: VerifiedResultKey,
    stored: tuple[VerifiedResultEntry, ...],
) -> tuple[str, ...]:
    """Return deterministic, content-free reasons for an ``INCOMPATIBLE`` lookup."""
    facts: set[str] = set()
    for entry in stored:
        facts.update(_fact_differences(requested, entry.key))
    return tuple(
        f"a stored verified result differs in the canonical fact {fact!r}"
        for fact in sorted(facts, key=_fact_order)
    )


# ---------------------------------------------------------------------------
# Stored evidence.
# ---------------------------------------------------------------------------


class EntryState(StrEnum):
    """Lifecycle state of one stored verified result.

    ``ACTIVE`` is the only state that can ever be reused. ``INVALIDATED`` is
    terminal for that entry: nothing reactivates it, and a later store under
    the same key creates a new entry with new evidence and new provenance
    instead of resurrecting the old one.

    Expiry is deliberately *not* a state. Freshness is a pure function of
    ``created_at``, ``ttl``, and the observed clock, so an entry can never be
    marked stale by an unrelated write and can never present itself as fresh
    after its boundary has passed.
    """

    ACTIVE = "active"
    INVALIDATED = "invalidated"


class InvalidationCause(StrEnum):
    """Controlled vocabulary for why an entry was invalidated."""

    EXPLICIT = "explicit"
    ENVIRONMENT_CHANGED = "environment_changed"


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidationRecord:
    """Explicit record of one terminal invalidation.

    ``reason`` is inert bounded text supplied by the invalidating caller. It is
    stored verbatim, never interpreted, and never echoed by lookups; it grants
    nothing and prohibits nothing on its own.
    """

    reason: str
    invalidated_at: datetime
    cause: InvalidationCause = InvalidationCause.EXPLICIT

    def __post_init__(self) -> None:
        _validate_text(self.reason, field_name="invalidation.reason", max_length=_MAX_REASON_LENGTH)
        object.__setattr__(
            self,
            "invalidated_at",
            _validate_timestamp(self.invalidated_at, field_name="invalidation.invalidated_at"),
        )
        if not isinstance(self.cause, InvalidationCause):
            raise TypeError(f"cause must be an InvalidationCause, got {type(self.cause).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedResultProvenance:
    """Where one stored verified result came from.

    Provenance is the *run* identity of the canonical execution that produced
    the evidence: the Task and the execution-context correlation id, plus the
    boundary that recorded the entry. It is evidence about the past, never a
    precondition for reuse (per-run identity is deliberately excluded from
    :class:`VerifiedResultKey`), and it grants no authority.

    The execution context's cancellation token and deadline are not provenance:
    they are execution-scoped control state and are never stored.
    """

    task_id: TaskId
    correlation_id: UUID
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError(f"task_id must be a TaskId, got {type(self.task_id).__name__}")
        if not isinstance(self.correlation_id, UUID):
            raise TypeError(
                f"correlation_id must be a UUID, got {type(self.correlation_id).__name__}"
            )
        if self.correlation_id.int == 0:
            raise VerifiedResultCacheValidationError("correlation_id must not be the nil UUID")
        _validate_text(self.source, field_name="provenance.source", max_length=_MAX_TEXT_LENGTH)


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedResultEntry:
    """One immutable, canonically verified, explicitly bounded reusable result.

    This is the only value the cache can hold, and it is a *proof-carrying*
    value: construction fails with :class:`VerifiedResultCacheRejection` unless
    the supplied :class:`~agentx.capabilities.runtime.ClosedLoopOutcome` is
    canonically verified. An unverified, denied, execution-failed, or
    verification-failed outcome therefore cannot become an entry at all, no
    matter what text it carries.

    The entry preserves the original outcome and evidence unchanged (the
    identical objects the canonical loop produced), the canonical verification
    verdict as a read-only property of that outcome, run provenance, the
    creation time and explicit freshness window, the complete canonical key
    (which carries capability/procedure version, scope, environment, and
    preconditions), and the invalidation state/reason once invalidated.

    Entries are immutable. Invalidation produces a new entry value; it never
    mutates a stored one, and it never rewrites the outcome or its verdict.
    """

    key: VerifiedResultKey
    outcome: ClosedLoopOutcome
    provenance: VerifiedResultProvenance
    created_at: datetime
    ttl: timedelta
    state: EntryState = EntryState.ACTIVE
    invalidation: InvalidationRecord | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, VerifiedResultKey):
            raise TypeError(f"key must be a VerifiedResultKey, got {type(self.key).__name__}")
        if not isinstance(self.outcome, ClosedLoopOutcome):
            raise TypeError(
                f"outcome must be a ClosedLoopOutcome, got {type(self.outcome).__name__}"
            )
        if not isinstance(self.provenance, VerifiedResultProvenance):
            raise TypeError(
                "provenance must be a VerifiedResultProvenance, "
                f"got {type(self.provenance).__name__}"
            )

        # THE invariant: only canonically verified evidence can become an entry.
        failures = _verified_evidence_failures(self.outcome, self.outcome.verification)
        if failures:
            raise VerifiedResultCacheRejection(
                "the execution result is not a canonically verified reusable result: "
                + "; ".join(failures)
            )

        task = self.outcome.task
        if task.task_id != self.provenance.task_id:
            raise VerifiedResultCacheValidationError(
                "provenance task identity does not match the outcome Task"
            )

        object.__setattr__(
            self, "created_at", _validate_timestamp(self.created_at, field_name="created_at")
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))

        if not isinstance(self.state, EntryState):
            raise TypeError(f"state must be an EntryState, got {type(self.state).__name__}")
        if self.invalidation is not None and not isinstance(self.invalidation, InvalidationRecord):
            raise TypeError(
                "invalidation must be an InvalidationRecord or None, "
                f"got {type(self.invalidation).__name__}"
            )
        if (self.state is EntryState.INVALIDATED) is not (self.invalidation is not None):
            raise VerifiedResultCacheValidationError(
                "state is INVALIDATED exactly when an invalidation record is present"
            )

    @property
    def fingerprint(self) -> str:
        """The stable digest of this entry's complete canonical key."""
        return self.key.fingerprint()

    @property
    def operation_fingerprint(self) -> str:
        """The stable digest of the weaker operation-plus-input identity."""
        return self.key.operation_fingerprint()

    @property
    def verification(self) -> VerificationResult:
        """The identical canonical verdict the run produced.

        This is a read-only projection of ``outcome.verification`` — never a
        stored copy, never re-parsed from text, and never reconstructible by a
        caller. There is no field in which a fabricated verdict could live.
        """
        verification = self.outcome.verification
        if not isinstance(verification, VerificationResult):  # pragma: no cover - gated above
            raise VerifiedResultCacheRejection("canonical verification evidence is missing")
        return verification

    @property
    def observation(self) -> CapabilityObservation:
        """The identical canonical observation evidence the run produced."""
        observation = self.outcome.observation
        if not isinstance(observation, CapabilityObservation):  # pragma: no cover - gated above
            raise VerifiedResultCacheRejection("observation evidence is missing")
        return observation

    @property
    def expires_at(self) -> datetime:
        """The explicit freshness boundary ``created_at + ttl``."""
        return self.created_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Return whether the entry is fresh at ``at``.

        Deterministic and fail closed: fresh exactly while ``at < expires_at``.
        The boundary instant itself is already stale, so an expired verified
        result can never present itself as reusable.
        """
        moment = _validate_timestamp(at, field_name="at")
        return moment < self.expires_at

    @property
    def evidence_is_verified(self) -> bool:
        """Re-check the stored canonical evidence (defence in depth).

        Construction already proved this once, and every stored value is
        immutable, so this can only become ``False`` if something in the
        process tampered with the stored outcome through ``object.__setattr__``.
        Re-checking costs a handful of typed field reads and makes a tampered
        entry unusable instead of reusable.
        """
        return not _verified_evidence_failures(self.outcome, self.outcome.verification)

    def is_reusable(self, at: datetime) -> bool:
        """Return whether this entry may be reused at ``at``.

        Reusable means: canonically verified evidence that still reads as
        canonically verified, an active (never invalidated) state, and a
        position inside the explicit freshness window. It never means
        "authorized": reuse of the operation still belongs to the governed
        runtime path.
        """
        return self.state is EntryState.ACTIVE and self.evidence_is_verified and self.is_fresh(at)

    def __hash__(self) -> int:
        return _digest_hash(self.key.canonical_text())


# ---------------------------------------------------------------------------
# Submission and lookup values.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedResultSubmission:
    """One request to cache an already-produced canonical outcome.

    The submission carries the canonical key material, the canonical A1.10
    outcome (read-only evidence), the :class:`~agentx.core.execution.ExecutionContext`
    of the run that produced it (used for provenance and for a task-identity
    consistency check only), and the explicit reuse window.

    It carries no authority, no permission, no risk or budget override, no
    verification requirement, no callable, and no model or free-text claim about
    success. ``created_at=None`` stamps the entry from the injected clock; an
    explicit ``created_at`` must be timezone-aware and is used verbatim.
    """

    key: VerifiedResultKey
    outcome: ClosedLoopOutcome
    context: ExecutionContext
    ttl: timedelta
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, VerifiedResultKey):
            raise TypeError(f"key must be a VerifiedResultKey, got {type(self.key).__name__}")
        if not isinstance(self.outcome, ClosedLoopOutcome):
            raise TypeError(
                f"outcome must be a ClosedLoopOutcome, got {type(self.outcome).__name__}"
            )
        if not isinstance(self.context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(self.context).__name__}"
            )
        _validate_ttl(self.ttl)
        if self.created_at is not None:
            _validate_timestamp(self.created_at, field_name="created_at")

        task: object = self.outcome.task
        if not isinstance(task, Task):
            raise VerifiedResultCacheValidationError(
                "the submitted outcome carries no canonical Task"
            )
        context_task_id = self.context.task_id
        if context_task_id is not None and context_task_id != task.task_id:
            raise VerifiedResultCacheValidationError(
                "execution context task identity does not match the outcome Task"
            )


class LookupStatus(StrEnum):
    """The five lookup outcomes a caller must never conflate."""

    MISS = "miss"
    VERIFIED_HIT = "verified_hit"
    STALE = "stale"
    INVALIDATED = "invalidated"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedResultLookup:
    """Immutable result of one cache lookup.

    ``entry`` is present for ``VERIFIED_HIT``, ``STALE``, and ``INVALIDATED``
    so a caller can inspect provenance, freshness, and invalidation state. It
    is diagnostic: **only** :attr:`reusable_outcome` may be consumed as a
    reusable verified result, and it is non-``None`` exactly for
    ``VERIFIED_HIT``. ``MISS`` and ``INCOMPATIBLE`` never carry an entry.

    ``reasons`` is empty exactly for ``VERIFIED_HIT``. Entries are
    deterministic, ordered, content-free strings naming canonical facts; they
    never echo observation content, error messages, verification detail, or
    invalidation reason text, so hostile strings cannot leak through a lookup.
    """

    status: LookupStatus
    fingerprint: str
    entry: VerifiedResultEntry | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, LookupStatus):
            raise TypeError(f"status must be a LookupStatus, got {type(self.status).__name__}")
        _validate_text(self.fingerprint, field_name="fingerprint", max_length=_MAX_TEXT_LENGTH)
        if self.entry is not None and not isinstance(self.entry, VerifiedResultEntry):
            raise TypeError(
                f"entry must be a VerifiedResultEntry or None, got {type(self.entry).__name__}"
            )
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) for reason in self.reasons
        ):
            raise TypeError("reasons must be a tuple of strings")
        if (self.status is LookupStatus.VERIFIED_HIT) is not (len(self.reasons) == 0):
            raise VerifiedResultCacheValidationError(
                "reasons must be empty exactly when the status is VERIFIED_HIT"
            )
        entry_required = self.status in (
            LookupStatus.VERIFIED_HIT,
            LookupStatus.STALE,
            LookupStatus.INVALIDATED,
        )
        if entry_required is not (self.entry is not None):
            raise VerifiedResultCacheValidationError(
                "an entry is present exactly for VERIFIED_HIT, STALE, and INVALIDATED lookups"
            )

    @property
    def is_reusable(self) -> bool:
        """Whether this lookup produced a reusable canonically verified result."""
        return self.status is LookupStatus.VERIFIED_HIT

    @property
    def reusable_outcome(self) -> ClosedLoopOutcome | None:
        """The reusable canonical outcome, or ``None`` for every other status.

        A returned outcome is always the identical object the canonical loop
        produced, with ``kind is LoopOutcome.VERIFIED``, ``verification.passed
        is True``, and ``task.status is TaskStatus.SUCCEEDED``. Reusing it is
        still not authority: re-presenting the operation to a user, a Task, or
        the kernel remains the governed runtime path's decision.
        """
        if self.status is not LookupStatus.VERIFIED_HIT or self.entry is None:
            return None
        return self.entry.outcome


# ---------------------------------------------------------------------------
# The cache.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class _StoredRecord:
    """Internal bookkeeping for one stored fingerprint.

    The cache keeps its own copy of the terminal invalidation record next to
    the entry value. Invalidation is therefore a fact about the cache's state,
    not only about a value object a caller holds: an entry tampered with inside
    the process cannot present itself as active again. The entry remains the
    only value callers ever see.
    """

    entry: VerifiedResultEntry
    invalidation: InvalidationRecord | None = None


class VerifiedResultCache:
    """Bounded in-memory cache of canonically verified, reusable results.

    The cache holds only :class:`VerifiedResultEntry` values, which cannot exist
    for unverified evidence. It has no collaborators, no kernel import, no
    registry, no capability handle, no event bus, and no audit sink: the only
    operations it performs are typed field reads, deterministic equality,
    canonical digestion, and dictionary bookkeeping under one lock.

    Reads never mutate entries. Expiry is lazy and there is no background
    worker, timer, or thread. A new instance observes nothing, so a process
    restart can never inherit verification evidence.
    """

    __slots__ = ("_clock", "_entries", "_families", "_lock", "_max_entries", "_source")

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _system_utc_now,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        source: str = VERIFIED_RESULT_CACHE_SOURCE,
    ) -> None:
        """Inject the clock, the bounded capacity, and the recording source.

        ``clock`` must return a timezone-aware datetime and is the only time
        source the cache reads, which makes freshness and invalidation fully
        deterministic in tests.
        """
        if not callable(clock):
            raise TypeError("clock must be a callable returning a timezone-aware datetime")
        if type(max_entries) is not int:
            raise TypeError(f"max_entries must be an int, got {type(max_entries).__name__}")
        if max_entries <= 0:
            raise VerifiedResultCacheValidationError("max_entries must be positive")
        self._clock = clock
        self._max_entries = max_entries
        self._source = _validate_text(source, field_name="source", max_length=_MAX_TEXT_LENGTH)
        self._entries: dict[str, _StoredRecord] = {}
        self._families: dict[str, set[str]] = {}
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Time.
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Read the injected clock exactly once and normalize it to UTC."""
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise VerifiedResultCacheClockError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    # ------------------------------------------------------------------
    # Store.
    # ------------------------------------------------------------------

    def store(self, submission: VerifiedResultSubmission) -> VerifiedResultEntry:
        """Cache one canonically verified result and return the stored entry.

        The entry is constructed — and therefore the verified-only invariant is
        enforced — before any cache state is touched, so a rejected submission
        leaves the cache exactly as it was.

        Raises:
            TypeError: for a non-:class:`VerifiedResultSubmission` argument.
            VerifiedResultCacheRejection: when the evidence is not canonically
                verified, or when it would replay evidence created at or before
                a recorded invalidation / older than the recorded entry.
            VerifiedResultCacheCapacityError: when the bounded cache is full
                after dropping entries whose freshness has already expired.
        """
        if not isinstance(submission, VerifiedResultSubmission):
            raise TypeError(
                f"submission must be a VerifiedResultSubmission, got {type(submission).__name__}"
            )

        now = self._now()
        created_at = (
            now
            if submission.created_at is None
            else _validate_timestamp(submission.created_at, field_name="created_at")
        )
        outcome = submission.outcome
        task: object = outcome.task
        if not isinstance(task, Task):
            raise VerifiedResultCacheRejection("the submitted outcome carries no canonical Task")

        # Construction is the gate: this raises before any mutation below.
        entry = VerifiedResultEntry(
            key=submission.key,
            outcome=outcome,
            provenance=VerifiedResultProvenance(
                task_id=task.task_id,
                correlation_id=submission.context.correlation_id,
                source=self._source,
            ),
            created_at=created_at,
            ttl=submission.ttl,
        )

        fingerprint = entry.fingerprint
        with self._lock:
            record = self._entries.get(fingerprint)
            if record is not None:
                invalidation = record.invalidation
                if invalidation is not None and created_at <= invalidation.invalidated_at:
                    raise VerifiedResultCacheRejection(
                        "evidence created at or before the recorded invalidation cannot be "
                        "re-cached under the same key"
                    )
                if record.entry.created_at > created_at:
                    raise VerifiedResultCacheRejection(
                        "evidence older than the recorded entry cannot replace it"
                    )
                self._discard_locked(record)
            if len(self._entries) >= self._max_entries:
                self._purge_unfresh_locked(now)
                if len(self._entries) >= self._max_entries:
                    raise VerifiedResultCacheCapacityError(
                        f"the verified-result cache is full ({self._max_entries} entries); "
                        "the store was refused and nothing was cached"
                    )
            self._entries[fingerprint] = _StoredRecord(entry=entry)
            self._families.setdefault(entry.operation_fingerprint, set()).add(fingerprint)
        return entry

    # ------------------------------------------------------------------
    # Lookup.
    # ------------------------------------------------------------------

    def lookup(self, key: VerifiedResultKey) -> VerifiedResultLookup:
        """Classify one canonical key against the recorded evidence.

        Deterministic and side-effect free: reads never mutate, never drop, and
        never repair entries. An exact fingerprint match is classified by the
        cache's own terminal invalidation record first, then by a re-read of the
        canonical evidence, then by freshness (``INVALIDATED`` before ``STALE``,
        because an explicit invalidation is the more specific fact). Otherwise
        the weaker operation-plus-input family decides between ``INCOMPATIBLE``
        (something like this was verified under different canonical facts) and
        ``MISS`` (nothing was ever verified for this operation and input).
        """
        if not isinstance(key, VerifiedResultKey):
            raise TypeError(f"key must be a VerifiedResultKey, got {type(key).__name__}")

        now = self._now()
        fingerprint = key.fingerprint()
        with self._lock:
            record = self._entries.get(fingerprint)
            if record is not None:
                entry = record.entry
                if record.invalidation is not None or entry.state is EntryState.INVALIDATED:
                    return VerifiedResultLookup(
                        status=LookupStatus.INVALIDATED,
                        fingerprint=fingerprint,
                        entry=entry,
                        reasons=("the recorded verified result was invalidated",),
                    )
                if not entry.evidence_is_verified:
                    # Only reachable if stored evidence was tampered with inside
                    # the process. Tampered evidence is terminal, never reusable.
                    return VerifiedResultLookup(
                        status=LookupStatus.INVALIDATED,
                        fingerprint=fingerprint,
                        entry=entry,
                        reasons=("the recorded evidence is not canonically verified",),
                    )
                if entry.is_reusable(now):
                    return VerifiedResultLookup(
                        status=LookupStatus.VERIFIED_HIT,
                        fingerprint=fingerprint,
                        entry=entry,
                    )
                return VerifiedResultLookup(
                    status=LookupStatus.STALE,
                    fingerprint=fingerprint,
                    entry=entry,
                    reasons=("the recorded verified result is no longer fresh",),
                )
            family: AbstractSet[str] = self._families.get(key.operation_fingerprint(), frozenset())
            stored = tuple(
                self._entries[found].entry for found in sorted(family) if found in self._entries
            )

        if not stored:
            return VerifiedResultLookup(
                status=LookupStatus.MISS,
                fingerprint=fingerprint,
                reasons=("no verified result is recorded for this canonical key",),
            )
        return VerifiedResultLookup(
            status=LookupStatus.INCOMPATIBLE,
            fingerprint=fingerprint,
            reasons=_incompatibility_reasons(key, stored),
        )

    # ------------------------------------------------------------------
    # Invalidation.
    # ------------------------------------------------------------------

    def invalidate(
        self,
        key: VerifiedResultKey,
        *,
        reason: str,
        cause: InvalidationCause = InvalidationCause.EXPLICIT,
    ) -> VerifiedResultEntry | None:
        """Move one stored entry to its terminal invalidated state.

        Returns the invalidated entry, the already-invalidated entry
        (idempotent: the first reason wins), or ``None`` when nothing is stored
        under this exact canonical key. Invalidation never deletes evidence,
        never rewrites the stored outcome or its verdict, and grants nothing.
        """
        if not isinstance(key, VerifiedResultKey):
            raise TypeError(f"key must be a VerifiedResultKey, got {type(key).__name__}")
        validated_reason = _validate_text(
            reason, field_name="invalidation reason", max_length=_MAX_REASON_LENGTH
        )
        if not isinstance(cause, InvalidationCause):
            raise TypeError(f"cause must be an InvalidationCause, got {type(cause).__name__}")
        now = self._now()
        fingerprint = key.fingerprint()
        with self._lock:
            record = self._entries.get(fingerprint)
            if record is None or record.invalidation is not None:
                return None if record is None else record.entry
            invalidation = InvalidationRecord(
                reason=validated_reason, invalidated_at=now, cause=cause
            )
            invalidated = replace(
                record.entry, state=EntryState.INVALIDATED, invalidation=invalidation
            )
            self._entries[fingerprint] = _StoredRecord(entry=invalidated, invalidation=invalidation)
            return invalidated

    def invalidate_environment(
        self,
        environment: EnvironmentIdentity,
        *,
        reason: str,
    ) -> tuple[VerifiedResultEntry, ...]:
        """Invalidate every stored entry recorded for one environment identity.

        This is the explicit mechanism for "the environment changed, so previous
        verification says nothing about now": it is driven by typed identity
        equality, never by name matching, globbing, or text interpretation.
        Returns the newly invalidated entries in deterministic fingerprint
        order; already-invalidated entries are left untouched and are not
        returned.
        """
        if not isinstance(environment, EnvironmentIdentity):
            raise TypeError(
                f"environment must be an EnvironmentIdentity, got {type(environment).__name__}"
            )
        validated_reason = _validate_text(
            reason, field_name="invalidation reason", max_length=_MAX_REASON_LENGTH
        )
        now = self._now()
        invalidated: list[VerifiedResultEntry] = []
        with self._lock:
            for fingerprint in sorted(self._entries):
                record = self._entries[fingerprint]
                if record.invalidation is not None:
                    continue
                if record.entry.key.environment != environment:
                    continue
                invalidation = InvalidationRecord(
                    reason=validated_reason,
                    invalidated_at=now,
                    cause=InvalidationCause.ENVIRONMENT_CHANGED,
                )
                replacement = replace(
                    record.entry, state=EntryState.INVALIDATED, invalidation=invalidation
                )
                self._entries[fingerprint] = _StoredRecord(
                    entry=replacement, invalidation=invalidation
                )
                invalidated.append(replacement)
        return tuple(invalidated)

    # ------------------------------------------------------------------
    # Inspection.
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of stored entries, including stale and invalidated ones."""
        with self._lock:
            return len(self._entries)

    def snapshot(self) -> tuple[VerifiedResultEntry, ...]:
        """Return every stored entry in deterministic fingerprint order."""
        with self._lock:
            return tuple(self._entries[fingerprint].entry for fingerprint in sorted(self._entries))

    # ------------------------------------------------------------------
    # Internal bookkeeping (callers must hold the lock).
    # ------------------------------------------------------------------

    def _discard_locked(self, record: _StoredRecord) -> None:
        """Remove one record and keep the operation family index consistent."""
        fingerprint = record.entry.fingerprint
        self._entries.pop(fingerprint, None)
        operation = record.entry.operation_fingerprint
        family = self._families.get(operation)
        if family is None:
            return
        family.discard(fingerprint)
        if not family:
            del self._families[operation]

    def _purge_unfresh_locked(self, now: datetime) -> int:
        """Drop records whose freshness boundary has been reached or passed.

        Called only when the bounded cache needs room. This is not decay and not
        a retention policy: it removes exactly the entries that are already
        unusable, in deterministic order, and scores nothing.
        """
        purged = 0
        for fingerprint in sorted(self._entries):
            record = self._entries[fingerprint]
            if record.entry.is_fresh(now):
                continue
            self._discard_locked(record)
            purged += 1
        return purged
