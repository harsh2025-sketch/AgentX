"""Canonical procedure-execution trace and evidence contract (M3.02).

This module defines the ONE canonical, immutable vocabulary AgentX uses to
describe *what happened* during a single procedure run. It is a DATA CONTRACT
ONLY:

    - it never executes a procedure, node, capability, model, or research step;
    - it never interprets, compiles, or traverses a Procedure Graph;
    - it never persists, publishes, retries, repairs, promotes, or schedules;
    - it never reads a clock (every timestamp is supplied by the caller);
    - it never grants authority and never claims a Task succeeded.

Why the contract exists
-----------------------

Runtime procedure execution and procedure learning are later tasks. Before any
of them is wired, the historical vocabulary must exist exactly once, otherwise
each subsystem invents its own node-result class, its own step status, its own
"success" boolean, and its own text-based "verified" marker. This module is
that single vocabulary.

The truth model (deliberately non-collapsible)
----------------------------------------------

Five different facts stay five different typed things. Nothing in this module
derives one from another:

    1. NODE EXECUTED — :attr:`ProcedureStepDisposition.EXECUTED`. The node ran.
       It proves nothing about the world and nothing about verification.
    2. NODE EXTERNAL EFFECT OBSERVED — recorded only by explicit
       ``observation_evidence`` on the step. Observation is not verification.
    3. NODE VERIFIED — :attr:`ProcedureStepDisposition.VERIFIED`, which is
       structurally impossible without explicit ``verification_evidence``.
    4. PROCEDURE CONTROL TERMINATED —
       :class:`ProcedureRunDisposition`. Reaching an ``END`` node means control
       flow finished; it is not success and not verification.
    5. ORIGINAL TASK VERIFIED — :class:`ProcedureTaskVerification`, a
       *separate* field that defaults to
       :attr:`ProcedureTaskVerification.NOT_ASSESSED` and is structurally
       impossible to set to ``TASK_VERIFIED`` without explicit
       ``task_verification_evidence`` bound to a canonical ``TaskId``.

There is deliberately no ``success`` / ``succeeded`` / ``ok`` / ``passed``
field anywhere in this contract, and no method derives one. A run that reached
``END`` with every step ``VERIFIED`` still reports
``task_verification == NOT_ASSESSED`` unless a caller supplied explicit
task-verification evidence.

A historical ``VERIFIED`` step is *history*. It grants no future authority: it
does not authorize a replay, does not lower risk, does not widen a budget, and
does not bypass the Action Gate. Authority is owned exclusively by
``agentx.kernel``.

Boundary ownership
------------------

``agentx.core`` is the inward foundation, so this module imports only the
standard library and sibling ``agentx.core`` contracts. It never imports
``agentx.kernel``, ``agentx.capabilities``, ``agentx.cognition``,
``agentx.learning``, ``agentx.procedures``, ``agentx.hive``, or
``agentx.infrastructure``.

The Procedure Graph IR (A3.01) lives outward in ``agentx.procedures``. A trace
must still record *which kind of node* ran, so this module carries its own
closed, core-safe :class:`ExecutedNodeKind` / :class:`ExecutedEdgeKind`
vocabularies whose string values intentionally match the canonical A3.01
spellings. They are closed strings, not an import, and this module never
resolves them back to a graph. Node identity is likewise a bounded, validated
core-safe string, never a domain identifier and never authority.

Nothing here duplicates ``Task`` (A1.05), ``ProcedureRecord`` (C2.03),
``ProcedureGraph`` (A3.01), ``EpisodeRecord``, or any verification result type:
large canonical objects are referenced by stable identity, never copied.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from agentx.core.ids import ArtifactId, EpisodeId, ProcedureId, TaskId
from agentx.core.tasks import JsonValue

__all__ = [
    "MAX_BINDING_SNAPSHOT_BYTES",
    "MAX_BINDING_SNAPSHOT_DEPTH",
    "MAX_BINDING_SNAPSHOT_ITEMS",
    "MAX_ERROR_CODE_LENGTH",
    "MAX_EVIDENCE_REFERENCES",
    "MAX_NODE_ID_LENGTH",
    "MAX_PROCEDURE_RUN_STEPS",
    "MAX_RUN_METADATA_BYTES",
    "MAX_RUN_METADATA_ITEMS",
    "PROCEDURE_EXECUTION_SCHEMA_VERSION",
    "ExecutedEdgeKind",
    "ExecutedNodeKind",
    "ProcedureEvidenceKind",
    "ProcedureExecutionDeserializationError",
    "ProcedureExecutionEvidence",
    "ProcedureExecutionValidationError",
    "ProcedureRunDisposition",
    "ProcedureRunRecord",
    "ProcedureStepDisposition",
    "ProcedureStepRecord",
    "ProcedureStepTransition",
    "ProcedureTaskVerification",
    "UnsupportedProcedureExecutionSchemaVersionError",
]

# --------------------------------------------------------------------------
# Schema version and hard size bounds.
#
# Every collection in this contract is bounded. There is no unbounded mapping,
# no unbounded sequence, and no "unlimited" escape hatch: a trace is historical
# evidence, not a payload store.
# --------------------------------------------------------------------------

#: Schema version of the serialized procedure-execution representation.
PROCEDURE_EXECUTION_SCHEMA_VERSION: Final[int] = 1

#: Maximum number of step records in one run.
MAX_PROCEDURE_RUN_STEPS: Final[int] = 512

#: Maximum number of evidence references in any single evidence collection.
MAX_EVIDENCE_REFERENCES: Final[int] = 16

#: Maximum number of top-level keys in one step binding snapshot.
MAX_BINDING_SNAPSHOT_ITEMS: Final[int] = 32

#: Maximum nesting depth of any inert JSON mapping carried by this contract —
#: a step binding snapshot or run metadata (top level is depth 1).
MAX_BINDING_SNAPSHOT_DEPTH: Final[int] = 4

#: Maximum serialized size, in UTF-8 bytes, of one step binding snapshot.
MAX_BINDING_SNAPSHOT_BYTES: Final[int] = 4_096

#: Maximum number of top-level keys in one run metadata mapping.
MAX_RUN_METADATA_ITEMS: Final[int] = 16

#: Maximum serialized size, in UTF-8 bytes, of one run metadata mapping.
MAX_RUN_METADATA_BYTES: Final[int] = 2_048

#: Maximum length of a core-safe node identifier string.
MAX_NODE_ID_LENGTH: Final[int] = 128

#: Maximum length of a canonical error code recorded on a step.
MAX_ERROR_CODE_LENGTH: Final[int] = 128

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

_EVIDENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {"kind", "event_id", "episode_id", "artifact_id", "correlation_id"}
)

_TRANSITION_FIELDS: Final[frozenset[str]] = frozenset({"edge_kind", "target_node_id"})

_STEP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "step_index",
        "node_id",
        "node_kind",
        "disposition",
        "started_at",
        "ended_at",
        "binding_snapshot",
        "observation_evidence",
        "verification_evidence",
        "error_code",
        "transition",
    }
)

_RUN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "run_id",
        "procedure_id",
        "procedure_revision",
        "task_id",
        "correlation_id",
        "started_at",
        "ended_at",
        "steps",
        "disposition",
        "control_evidence",
        "task_verification",
        "task_verification_evidence",
        "metadata",
    }
)


class ProcedureExecutionValidationError(ValueError):
    """Raised when procedure-execution trace data violates this contract."""


class ProcedureExecutionDeserializationError(ProcedureExecutionValidationError):
    """Raised when encoded trace data cannot be reconstructed safely."""


class UnsupportedProcedureExecutionSchemaVersionError(ProcedureExecutionDeserializationError):
    """Raised when encoded trace data uses an unsupported schema version."""


# --------------------------------------------------------------------------
# Closed vocabularies.
# --------------------------------------------------------------------------


class ExecutedNodeKind(StrEnum):
    """Closed, core-safe vocabulary naming which kind of node a step ran.

    The string values intentionally match the canonical A3.01 procedure-graph
    node-kind spellings so a trace and a graph describe the same word. This is
    a closed string vocabulary, never an import of the outward Procedure Graph
    IR: this contract never resolves a kind back to a node definition, never
    interprets node semantics, and never executes anything.
    """

    ACTION = "action"
    OBSERVE = "observe"
    VERIFY = "verify"
    BRANCH = "branch"
    TRANSFORM = "transform"
    REASON = "reason"
    RESEARCH = "research"
    WAIT = "wait"
    ROLLBACK = "rollback"
    SUBPROCEDURE = "subprocedure"
    END = "end"


class ExecutedEdgeKind(StrEnum):
    """Closed, core-safe vocabulary naming which edge a step transitioned over.

    Mirrors the canonical A3.01 edge classification spellings (normal
    progression versus recovery/repair path) as closed strings. Recording that
    a recovery edge was taken is history; it authorizes no retry.
    """

    NEXT = "next"
    RECOVERY = "recovery"


class ProcedureStepDisposition(StrEnum):
    """Closed, externally supplied historical disposition of one step.

    Every member is *reported by the caller*; this contract never derives,
    upgrades, or downgrades a disposition, and never infers one from text.

    The members keep execution, observation, verification, control, and
    "work is required elsewhere" strictly distinct:

        EXECUTED — the node ran and produced no verification verdict. This is
            NOT success and NOT verification. Verification evidence is
            structurally forbidden on an ``EXECUTED`` step.
        VERIFIED — canonical verification explicitly confirmed the node's
            expected postcondition. Structurally impossible without explicit
            ``verification_evidence``. Historical only: it grants no authority
            for any future action.
        VERIFICATION_FAILED — verification ran and did not confirm the
            postcondition. Requires verification evidence or a canonical error
            code.
        EXECUTION_FAILED — the node's execution itself failed. Requires a
            canonical error code.
        DENIED — the step was refused before execution (authority, gate, stop,
            or budget refusal recorded elsewhere). Requires a canonical error
            code. Nothing executed.
        CANCELLED — the step was cooperatively cancelled.
        TIMED_OUT — the step exceeded its deadline.
        REASON_REQUIRED / RESEARCH_REQUIRED / SUBPROCEDURE_REQUIRED — the node
            reported that reasoning, research, or a subprocedure is required
            before the procedure can continue. These are requirements observed
            in history, never instructions, never authority, and never
            outcomes: no verification evidence and no error code may be
            attached to them.
        SKIPPED_BY_BRANCH — control flow did not take this node, so it never
            ran. No observation, no verification, and no error may be attached.
    """

    EXECUTED = "executed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    EXECUTION_FAILED = "execution_failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REASON_REQUIRED = "reason_required"
    RESEARCH_REQUIRED = "research_required"
    SUBPROCEDURE_REQUIRED = "subprocedure_required"
    SKIPPED_BY_BRANCH = "skipped_by_branch"


class ProcedureRunDisposition(StrEnum):
    """Closed terminal *control-flow* disposition of one procedure run.

    This vocabulary answers only "how did procedure control stop?". It never
    answers "did the task succeed?" — that question has its own separate field
    (:class:`ProcedureTaskVerification`).

        REACHED_END — control reached a terminal ``END`` node. Control
            terminated; nothing is proven about the world or the task.
        HALTED_ON_STEP_FAILURE — control stopped because the final recorded
            step failed, was denied, or timed out.
        DENIED — the run was refused; either nothing ran or the final recorded
            step was denied.
        CANCELLED — the run was cooperatively cancelled.
        TIMED_OUT — the run exceeded its deadline.
    """

    REACHED_END = "reached_end"
    HALTED_ON_STEP_FAILURE = "halted_on_step_failure"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ProcedureTaskVerification(StrEnum):
    """Closed, separate vocabulary for whether the ORIGINAL TASK was verified.

    Deliberately independent of :class:`ProcedureRunDisposition` and of every
    step disposition. It is fail-closed: absence of an explicit assessment is
    :attr:`NOT_ASSESSED`, never an implied success.

        NOT_ASSESSED — no explicit task-level verification was recorded. This
            is the default and the only value allowed without evidence.
        TASK_VERIFIED — task-level verification explicitly confirmed the
            original task's expected outcome. Requires both a canonical
            ``task_id`` and explicit ``task_verification_evidence``.
        TASK_VERIFICATION_FAILED — task-level verification explicitly did not
            confirm it. Requires the same explicit references.
    """

    NOT_ASSESSED = "not_assessed"
    TASK_VERIFIED = "task_verified"
    TASK_VERIFICATION_FAILED = "task_verification_failed"


class ProcedureEvidenceKind(StrEnum):
    """Closed vocabulary of evidence references a trace may carry.

    Each member binds to exactly one already-canonical identity. Evidence is a
    *pointer to a record that exists elsewhere*: this contract never
    dereferences, fetches, opens, ranks, or trusts it, and never copies the
    referenced object into the trace.

        EVENT — a canonical C1.02 ``Event`` identity (UUID).
        CHAIN_CORRELATION — the canonical execution-chain correlation UUID.
        EPISODE — a canonical ``EpisodeId``.
        ARTIFACT — a canonical ``ArtifactId``.
    """

    EVENT = "event"
    CHAIN_CORRELATION = "chain_correlation"
    EPISODE = "episode"
    ARTIFACT = "artifact"


#: Dispositions on which verification evidence is meaningful. Every other
#: disposition structurally forbids it, so "executed" can never be read as
#: "verified".
_VERIFICATION_DISPOSITIONS: Final[frozenset[ProcedureStepDisposition]] = frozenset(
    {
        ProcedureStepDisposition.VERIFIED,
        ProcedureStepDisposition.VERIFICATION_FAILED,
    }
)

#: Dispositions that require an explicit canonical error code.
_ERROR_REQUIRED_DISPOSITIONS: Final[frozenset[ProcedureStepDisposition]] = frozenset(
    {
        ProcedureStepDisposition.EXECUTION_FAILED,
        ProcedureStepDisposition.DENIED,
    }
)

#: Dispositions that must never carry an error code.
_ERROR_FORBIDDEN_DISPOSITIONS: Final[frozenset[ProcedureStepDisposition]] = frozenset(
    {
        ProcedureStepDisposition.EXECUTED,
        ProcedureStepDisposition.VERIFIED,
        ProcedureStepDisposition.REASON_REQUIRED,
        ProcedureStepDisposition.RESEARCH_REQUIRED,
        ProcedureStepDisposition.SUBPROCEDURE_REQUIRED,
        ProcedureStepDisposition.SKIPPED_BY_BRANCH,
    }
)

#: Dispositions that mean control could not continue past this step.
_HALTING_DISPOSITIONS: Final[frozenset[ProcedureStepDisposition]] = frozenset(
    {
        ProcedureStepDisposition.EXECUTION_FAILED,
        ProcedureStepDisposition.VERIFICATION_FAILED,
        ProcedureStepDisposition.DENIED,
        ProcedureStepDisposition.TIMED_OUT,
    }
)


# --------------------------------------------------------------------------
# Shared validation/serialization helpers (pure, no I/O, no clock).
# --------------------------------------------------------------------------


def _ensure_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], record_name: str
) -> None:
    """Reject missing and unknown fields; encoded data must match exactly."""
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    if missing:
        raise ProcedureExecutionDeserializationError(
            f"{record_name} missing required fields: {sorted(missing)}"
        )
    raise ProcedureExecutionDeserializationError(
        f"{record_name} contains unknown fields: {sorted(actual - expected)}"
    )


def _ensure_object(value: object, *, field_name: str) -> dict[str, object]:
    """Return a plain string-keyed copy of a JSON object, or fail closed."""
    if not isinstance(value, Mapping):
        raise ProcedureExecutionDeserializationError(f"{field_name} must be a JSON object")
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ProcedureExecutionDeserializationError(
                f"{field_name} contains a non-string object key"
            )
        copied[key] = item
    return copied


def _ensure_integer(value: object, *, field_name: str) -> int:
    """Require a real integer. ``bool`` is rejected: it is not an index."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcedureExecutionValidationError(f"{field_name} must be an integer")
    return value


def _ensure_timestamp(value: object, *, field_name: str) -> datetime:
    """Require a timezone-aware datetime, normalized to UTC."""
    if not isinstance(value, datetime):
        raise ProcedureExecutionValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureExecutionValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ProcedureExecutionDeserializationError(f"{field_name} must be an ISO-8601 string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProcedureExecutionDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProcedureExecutionDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ensure_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ProcedureExecutionValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ProcedureExecutionValidationError(f"{field_name} must not be the nil UUID")
    return value


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise ProcedureExecutionDeserializationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ProcedureExecutionDeserializationError(f"{field_name} is not a valid UUID") from exc
    if parsed.int == 0:
        raise ProcedureExecutionDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _ensure_bounded_text(value: object, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ProcedureExecutionValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ProcedureExecutionValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > limit:
        raise ProcedureExecutionValidationError(f"{field_name} must be at most {limit} characters")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ProcedureExecutionValidationError(f"{field_name} must not contain control characters")
    return value


def _ensure_node_id(value: object, *, field_name: str) -> str:
    """Validate a core-safe node identifier string.

    A node id is a bounded, trimmed, control-character-free string local to a
    procedure graph. It is not a domain identifier, it is never resolved to a
    node definition, and its content — including hostile content — is inert.
    """
    return _ensure_bounded_text(value, field_name=field_name, limit=MAX_NODE_ID_LENGTH)


def _ensure_optional_error_code(value: object) -> str | None:
    """Validate an optional canonical error code (dot-separated, inert text)."""
    if value is None:
        return None
    return _ensure_bounded_text(value, field_name="error_code", limit=MAX_ERROR_CODE_LENGTH)


def _freeze_json(value: object, *, path: str, depth: int, max_depth: int) -> object:
    """Deep-validate JSON compatibility and return an immutable deep copy."""
    if depth > max_depth:
        raise ProcedureExecutionValidationError(
            f"{path} exceeds the maximum nesting depth of {max_depth}"
        )
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProcedureExecutionValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProcedureExecutionValidationError(f"{path} contains a non-string key")
            if not key or key != key.strip():
                raise ProcedureExecutionValidationError(
                    f"{path} contains an empty or untrimmed key"
                )
            frozen[key] = _freeze_json(
                item, path=f"{path}.{key}", depth=depth + 1, max_depth=max_depth
            )
        return MappingProxyType(frozen)
    if isinstance(value, tuple | list):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1, max_depth=max_depth)
            for index, item in enumerate(value)
        )
    raise ProcedureExecutionValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_value(value: object, *, path: str) -> JsonValue:
    """Convert an internally frozen JSON value back to plain JSON primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive: construction already rejects this
            raise ProcedureExecutionValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive: construction already rejects this
                raise ProcedureExecutionValidationError(f"{path} contains a non-string key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise ProcedureExecutionValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, JsonValue]:
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mappings always convert to dict
        raise AssertionError("JSON object conversion produced a non-dict")
    return converted


def _measure_json_bytes(value: Mapping[str, object], *, path: str) -> int:
    encoded = json.dumps(
        _to_json_object(value, path=path),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(encoded.encode("utf-8"))


def _freeze_bounded_object(
    value: object,
    *,
    path: str,
    max_items: int,
    max_bytes: int,
    max_depth: int = MAX_BINDING_SNAPSHOT_DEPTH,
) -> Mapping[str, object]:
    """Validate, bound, and deep-freeze one inert JSON-compatible mapping.

    Content is data. Hostile strings are preserved verbatim as characters; they
    are never parsed, interpreted, or allowed to change a typed disposition.
    """
    if not isinstance(value, Mapping):
        raise ProcedureExecutionValidationError(f"{path} must be a mapping")
    if len(value) > max_items:
        raise ProcedureExecutionValidationError(f"{path} must have at most {max_items} keys")
    frozen = _freeze_json(value, path=path, depth=1, max_depth=max_depth)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("freezing a mapping produced a non-mapping")
    size = _measure_json_bytes(frozen, path=path)
    if size > max_bytes:
        raise ProcedureExecutionValidationError(
            f"{path} must serialize to at most {max_bytes} bytes (got {size})"
        )
    return frozen


def _ensure_evidence_tuple(
    value: object, *, field_name: str
) -> tuple[ProcedureExecutionEvidence, ...]:
    if not isinstance(value, tuple):
        raise ProcedureExecutionValidationError(
            f"{field_name} must be a tuple of ProcedureExecutionEvidence values"
        )
    if len(value) > MAX_EVIDENCE_REFERENCES:
        raise ProcedureExecutionValidationError(
            f"{field_name} must contain at most {MAX_EVIDENCE_REFERENCES} references"
        )
    for index, item in enumerate(value):
        if not isinstance(item, ProcedureExecutionEvidence):
            raise ProcedureExecutionValidationError(
                f"{field_name}[{index}] must be a ProcedureExecutionEvidence"
            )
    return value


def _parse_evidence_tuple(
    value: object, *, field_name: str
) -> tuple[ProcedureExecutionEvidence, ...]:
    if not isinstance(value, list):
        raise ProcedureExecutionDeserializationError(f"{field_name} must be a JSON array")
    if len(value) > MAX_EVIDENCE_REFERENCES:
        raise ProcedureExecutionDeserializationError(
            f"{field_name} must contain at most {MAX_EVIDENCE_REFERENCES} references"
        )
    return tuple(
        ProcedureExecutionEvidence.from_dict(
            _ensure_object(item, field_name=f"{field_name}[{index}]")
        )
        for index, item in enumerate(value)
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """Build a plain ``dict`` from decoded pairs, rejecting duplicate keys.

    This is a *pairs* hook, not an object hook: it constructs no custom type,
    imports nothing, and calls nothing. Its only purpose is to make the strict
    exact-field validation unbypassable, because a stock JSON decoder would
    silently let a repeated key overwrite an earlier value.
    """
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ProcedureExecutionDeserializationError(
                f"encoded trace contains a duplicate object key: {key!r}"
            )
        result[key] = value
    return result


def _reject_non_finite_literal(name: str) -> float:
    """Reject ``NaN`` / ``Infinity`` literals, which JSON text must never carry."""
    raise ProcedureExecutionDeserializationError(
        f"encoded trace contains a non-finite float literal: {name}"
    )


def _decode_json_object(raw: object, *, record_name: str) -> dict[str, object]:
    """Decode deterministic JSON text into a strict, duplicate-free object."""
    if not isinstance(raw, str):
        raise ProcedureExecutionDeserializationError(f"{record_name} JSON must be a string")
    try:
        decoded: object = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_literal,
        )
    except json.JSONDecodeError as exc:
        raise ProcedureExecutionDeserializationError(f"{record_name} JSON is malformed") from exc
    if not isinstance(decoded, dict):
        raise ProcedureExecutionDeserializationError(f"{record_name} JSON root must be an object")
    return _ensure_object(decoded, field_name=record_name)


def _dump_json(payload: Mapping[str, JsonValue]) -> str:
    """Serialize to deterministic JSON: sorted keys, no NaN, compact."""
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


# --------------------------------------------------------------------------
# Evidence reference.
# --------------------------------------------------------------------------


def _present_evidence_references(
    *,
    event_id: UUID | None,
    correlation_id: UUID | None,
    episode_id: EpisodeId | None,
    artifact_id: ArtifactId | None,
) -> frozenset[str]:
    present: set[str] = set()
    if event_id is not None:
        present.add("event_id")
    if correlation_id is not None:
        present.add("correlation_id")
    if episode_id is not None:
        present.add("episode_id")
    if artifact_id is not None:
        present.add("artifact_id")
    return frozenset(present)


_EVIDENCE_REQUIRED_REFERENCE: Final[Mapping[ProcedureEvidenceKind, str]] = MappingProxyType(
    {
        ProcedureEvidenceKind.EVENT: "event_id",
        ProcedureEvidenceKind.CHAIN_CORRELATION: "correlation_id",
        ProcedureEvidenceKind.EPISODE: "episode_id",
        ProcedureEvidenceKind.ARTIFACT: "artifact_id",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureExecutionEvidence:
    """One typed pointer to canonical evidence that already exists elsewhere.

    An evidence item pairs a closed :class:`ProcedureEvidenceKind` with exactly
    one matching canonical reference; foreign or extra references are rejected
    so a hostile payload cannot smuggle unrelated identity claims into a fact.

    An evidence item is inert. Constructing, comparing, serializing, or
    decoding one dereferences nothing, fetches nothing, executes nothing, and
    proves nothing on its own: it records only *where* the supporting record
    can be found.
    """

    kind: ProcedureEvidenceKind
    event_id: UUID | None = None
    correlation_id: UUID | None = None
    episode_id: EpisodeId | None = None
    artifact_id: ArtifactId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProcedureEvidenceKind):
            raise ProcedureExecutionValidationError(
                "kind must be a ProcedureEvidenceKind member; "
                "this contract never infers evidence from text"
            )
        if self.event_id is not None:
            object.__setattr__(self, "event_id", _ensure_uuid(self.event_id, field_name="event_id"))
        if self.correlation_id is not None:
            object.__setattr__(
                self,
                "correlation_id",
                _ensure_uuid(self.correlation_id, field_name="correlation_id"),
            )
        if self.episode_id is not None and not isinstance(self.episode_id, EpisodeId):
            raise ProcedureExecutionValidationError("episode_id must be an EpisodeId")
        if self.artifact_id is not None and not isinstance(self.artifact_id, ArtifactId):
            raise ProcedureExecutionValidationError("artifact_id must be an ArtifactId")

        required = _EVIDENCE_REQUIRED_REFERENCE[self.kind]
        present = _present_evidence_references(
            event_id=self.event_id,
            correlation_id=self.correlation_id,
            episode_id=self.episode_id,
            artifact_id=self.artifact_id,
        )
        if required not in present:
            raise ProcedureExecutionValidationError(
                f"{self.kind.value} evidence requires an explicit {required}"
            )
        if present - {required}:
            raise ProcedureExecutionValidationError(
                f"{self.kind.value} evidence must not carry foreign reference fields"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "kind": self.kind.value,
            "event_id": None if self.event_id is None else str(self.event_id),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "artifact_id": None if self.artifact_id is None else self.artifact_id.to_str(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureExecutionEvidence:
        """Strictly reconstruct one evidence item, failing closed."""
        _ensure_exact_fields(raw, expected=_EVIDENCE_FIELDS, record_name="evidence")

        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ProcedureExecutionDeserializationError("evidence kind must be a string")
        try:
            kind = ProcedureEvidenceKind(kind_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown evidence kind: {kind_raw!r}"
            ) from exc

        event_raw = raw["event_id"]
        correlation_raw = raw["correlation_id"]
        episode_raw = raw["episode_id"]
        artifact_raw = raw["artifact_id"]

        episode_id: EpisodeId | None = None
        if episode_raw is not None:
            if not isinstance(episode_raw, str):
                raise ProcedureExecutionDeserializationError("episode_id must be a string or null")
            try:
                episode_id = EpisodeId.parse(episode_raw)
            except ValueError as exc:
                raise ProcedureExecutionDeserializationError(
                    "episode_id is not a valid EpisodeId"
                ) from exc

        artifact_id: ArtifactId | None = None
        if artifact_raw is not None:
            if not isinstance(artifact_raw, str):
                raise ProcedureExecutionDeserializationError("artifact_id must be a string or null")
            try:
                artifact_id = ArtifactId.parse(artifact_raw)
            except ValueError as exc:
                raise ProcedureExecutionDeserializationError(
                    "artifact_id is not a valid ArtifactId"
                ) from exc

        try:
            return cls(
                kind=kind,
                event_id=(
                    None if event_raw is None else _parse_uuid(event_raw, field_name="event_id")
                ),
                correlation_id=(
                    None
                    if correlation_raw is None
                    else _parse_uuid(correlation_raw, field_name="correlation_id")
                ),
                episode_id=episode_id,
                artifact_id=artifact_id,
            )
        except ProcedureExecutionDeserializationError:
            raise
        except ProcedureExecutionValidationError as exc:
            raise ProcedureExecutionDeserializationError(f"evidence is invalid: {exc}") from exc


# --------------------------------------------------------------------------
# Step transition.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureStepTransition:
    """The transition control actually took away from one step.

    Records which closed edge classification was followed and which node id
    control moved to. Historical only: recording a ``RECOVERY`` transition
    authorizes no retry, and this contract never resolves the target id.
    """

    edge_kind: ExecutedEdgeKind
    target_node_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.edge_kind, ExecutedEdgeKind):
            raise ProcedureExecutionValidationError("edge_kind must be an ExecutedEdgeKind")
        object.__setattr__(
            self,
            "target_node_id",
            _ensure_node_id(self.target_node_id, field_name="target_node_id"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {"edge_kind": self.edge_kind.value, "target_node_id": self.target_node_id}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureStepTransition:
        """Strictly reconstruct one transition, failing closed."""
        _ensure_exact_fields(raw, expected=_TRANSITION_FIELDS, record_name="transition")
        edge_raw = raw["edge_kind"]
        if not isinstance(edge_raw, str):
            raise ProcedureExecutionDeserializationError("edge_kind must be a string")
        try:
            edge_kind = ExecutedEdgeKind(edge_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown edge kind: {edge_raw!r}"
            ) from exc
        target_raw = raw["target_node_id"]
        if not isinstance(target_raw, str):
            raise ProcedureExecutionDeserializationError("target_node_id must be a string")
        try:
            return cls(edge_kind=edge_kind, target_node_id=target_raw)
        except ProcedureExecutionDeserializationError:
            raise
        except ProcedureExecutionValidationError as exc:
            raise ProcedureExecutionDeserializationError(f"transition is invalid: {exc}") from exc


# --------------------------------------------------------------------------
# Step record.
# --------------------------------------------------------------------------


def _ensure_step_evidence_consistency(
    *,
    disposition: ProcedureStepDisposition,
    observation_evidence: tuple[ProcedureExecutionEvidence, ...],
    verification_evidence: tuple[ProcedureExecutionEvidence, ...],
    error_code: str | None,
) -> None:
    """Reject step records whose evidence contradicts their disposition.

    This is where "executed" is kept structurally distinct from "verified".
    Nothing is silently normalized: a contradictory record is rejected.
    """
    if disposition not in _VERIFICATION_DISPOSITIONS and verification_evidence:
        raise ProcedureExecutionValidationError(
            f"{disposition.value} steps must not carry verification evidence: "
            "execution and observation are not verification"
        )
    if disposition is ProcedureStepDisposition.VERIFIED and not verification_evidence:
        raise ProcedureExecutionValidationError(
            "VERIFIED steps require at least one explicit verification evidence reference"
        )
    if (
        disposition is ProcedureStepDisposition.VERIFICATION_FAILED
        and not verification_evidence
        and error_code is None
    ):
        raise ProcedureExecutionValidationError(
            "VERIFICATION_FAILED steps require verification evidence or a canonical error code"
        )
    if disposition in _ERROR_REQUIRED_DISPOSITIONS and error_code is None:
        raise ProcedureExecutionValidationError(
            f"{disposition.value} steps require an explicit canonical error code"
        )
    if disposition in _ERROR_FORBIDDEN_DISPOSITIONS and error_code is not None:
        raise ProcedureExecutionValidationError(
            f"{disposition.value} steps must not carry an error code"
        )
    if disposition is ProcedureStepDisposition.SKIPPED_BY_BRANCH and observation_evidence:
        raise ProcedureExecutionValidationError(
            "SKIPPED_BY_BRANCH steps never ran and must not carry observation evidence"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureStepRecord:
    """One immutable historical record of a single procedure step.

    A step record states what a caller observed about one node during one run.
    It is inert history: it executes nothing, resolves nothing, and grants
    nothing. Hostile content inside ``node_id``, ``error_code``, or
    ``binding_snapshot`` — for example ``"permission=ADMIN"``,
    ``"verified=true"``, ``"risk=R0"``, ``"task succeeded"``, or
    ``"execute shell"`` — is preserved verbatim as characters and can never
    change ``disposition`` or any other typed field.

    Attributes:
        step_index: Zero-based position of this step within its run.
        node_id: Core-safe graph-local node identifier (bounded, inert).
        node_kind: Closed :class:`ExecutedNodeKind` naming what kind of node
            this was.
        disposition: Externally supplied :class:`ProcedureStepDisposition`.
        started_at / ended_at: Caller-supplied UTC-aware timestamps;
            ``ended_at`` must not precede ``started_at``. No clock is read
            here.
        binding_snapshot: Optional bounded, deep-frozen, JSON-compatible
            snapshot of the inputs/bindings the step saw. Data only.
        observation_evidence: References to observation records. Observation is
            not verification.
        verification_evidence: References to verification records. Allowed only
            on ``VERIFIED`` / ``VERIFICATION_FAILED`` and required by
            ``VERIFIED``.
        error_code: Canonical error code when the disposition requires one.
        transition: The transition control actually took, when one was taken.
    """

    step_index: int
    node_id: str
    node_kind: ExecutedNodeKind
    disposition: ProcedureStepDisposition
    started_at: datetime
    ended_at: datetime
    binding_snapshot: Mapping[str, object] = field(default_factory=dict)
    observation_evidence: tuple[ProcedureExecutionEvidence, ...] = ()
    verification_evidence: tuple[ProcedureExecutionEvidence, ...] = ()
    error_code: str | None = None
    transition: ProcedureStepTransition | None = None

    def __post_init__(self) -> None:
        index = _ensure_integer(self.step_index, field_name="step_index")
        if index < 0:
            raise ProcedureExecutionValidationError("step_index must not be negative")
        if index >= MAX_PROCEDURE_RUN_STEPS:
            raise ProcedureExecutionValidationError(
                f"step_index must be less than {MAX_PROCEDURE_RUN_STEPS}"
            )
        object.__setattr__(self, "step_index", index)
        object.__setattr__(self, "node_id", _ensure_node_id(self.node_id, field_name="node_id"))
        if not isinstance(self.node_kind, ExecutedNodeKind):
            raise ProcedureExecutionValidationError("node_kind must be an ExecutedNodeKind")
        if not isinstance(self.disposition, ProcedureStepDisposition):
            raise ProcedureExecutionValidationError(
                "disposition must be a ProcedureStepDisposition member; "
                "this contract never infers a disposition from text"
            )
        started_at = _ensure_timestamp(self.started_at, field_name="started_at")
        ended_at = _ensure_timestamp(self.ended_at, field_name="ended_at")
        if ended_at < started_at:
            raise ProcedureExecutionValidationError("ended_at must not precede started_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        object.__setattr__(
            self,
            "binding_snapshot",
            _freeze_bounded_object(
                self.binding_snapshot,
                path="binding_snapshot",
                max_items=MAX_BINDING_SNAPSHOT_ITEMS,
                max_bytes=MAX_BINDING_SNAPSHOT_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "observation_evidence",
            _ensure_evidence_tuple(self.observation_evidence, field_name="observation_evidence"),
        )
        object.__setattr__(
            self,
            "verification_evidence",
            _ensure_evidence_tuple(self.verification_evidence, field_name="verification_evidence"),
        )
        object.__setattr__(self, "error_code", _ensure_optional_error_code(self.error_code))
        if self.transition is not None and not isinstance(self.transition, ProcedureStepTransition):
            raise ProcedureExecutionValidationError(
                "transition must be a ProcedureStepTransition or None"
            )
        _ensure_step_evidence_consistency(
            disposition=self.disposition,
            observation_evidence=self.observation_evidence,
            verification_evidence=self.verification_evidence,
            error_code=self.error_code,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "step_index": self.step_index,
            "node_id": self.node_id,
            "node_kind": self.node_kind.value,
            "disposition": self.disposition.value,
            "started_at": _format_timestamp(self.started_at),
            "ended_at": _format_timestamp(self.ended_at),
            "binding_snapshot": _to_json_object(self.binding_snapshot, path="binding_snapshot"),
            "observation_evidence": [item.to_dict() for item in self.observation_evidence],
            "verification_evidence": [item.to_dict() for item in self.verification_evidence],
            "error_code": self.error_code,
            "transition": None if self.transition is None else self.transition.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize this step to deterministic JSON text."""
        return _dump_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureStepRecord:
        """Strictly reconstruct one step record, failing closed."""
        _ensure_exact_fields(raw, expected=_STEP_FIELDS, record_name="step")

        kind_raw = raw["node_kind"]
        if not isinstance(kind_raw, str):
            raise ProcedureExecutionDeserializationError("node_kind must be a string")
        try:
            node_kind = ExecutedNodeKind(kind_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown node kind: {kind_raw!r}"
            ) from exc

        disposition_raw = raw["disposition"]
        if not isinstance(disposition_raw, str):
            raise ProcedureExecutionDeserializationError("disposition must be a string")
        try:
            disposition = ProcedureStepDisposition(disposition_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown step disposition: {disposition_raw!r}"
            ) from exc

        error_raw = raw["error_code"]
        if error_raw is not None and not isinstance(error_raw, str):
            raise ProcedureExecutionDeserializationError("error_code must be a string or null")

        node_id_raw = raw["node_id"]
        if not isinstance(node_id_raw, str):
            raise ProcedureExecutionDeserializationError("node_id must be a string")

        transition_raw = raw["transition"]
        transition = (
            None
            if transition_raw is None
            else ProcedureStepTransition.from_dict(
                _ensure_object(transition_raw, field_name="transition")
            )
        )

        try:
            return cls(
                step_index=_ensure_integer(raw["step_index"], field_name="step_index"),
                node_id=node_id_raw,
                node_kind=node_kind,
                disposition=disposition,
                started_at=_parse_timestamp(raw["started_at"], field_name="started_at"),
                ended_at=_parse_timestamp(raw["ended_at"], field_name="ended_at"),
                binding_snapshot=_ensure_object(
                    raw["binding_snapshot"], field_name="binding_snapshot"
                ),
                observation_evidence=_parse_evidence_tuple(
                    raw["observation_evidence"], field_name="observation_evidence"
                ),
                verification_evidence=_parse_evidence_tuple(
                    raw["verification_evidence"], field_name="verification_evidence"
                ),
                error_code=error_raw,
                transition=transition,
            )
        except ProcedureExecutionDeserializationError:
            raise
        except ProcedureExecutionValidationError as exc:
            raise ProcedureExecutionDeserializationError(f"step is invalid: {exc}") from exc

    @classmethod
    def from_json(cls, raw: str) -> ProcedureStepRecord:
        """Deserialize deterministic JSON text into an inert step record."""
        return cls.from_dict(_decode_json_object(raw, record_name="step"))


# --------------------------------------------------------------------------
# Run record.
# --------------------------------------------------------------------------


def _ensure_step_sequence(
    value: object, *, started_at: datetime, ended_at: datetime
) -> tuple[ProcedureStepRecord, ...]:
    """Validate an ordered, contiguous, in-window sequence of step records."""
    if not isinstance(value, tuple):
        raise ProcedureExecutionValidationError("steps must be a tuple of ProcedureStepRecord")
    if len(value) > MAX_PROCEDURE_RUN_STEPS:
        raise ProcedureExecutionValidationError(
            f"steps must contain at most {MAX_PROCEDURE_RUN_STEPS} records"
        )
    previous_started_at: datetime | None = None
    for position, step in enumerate(value):
        if not isinstance(step, ProcedureStepRecord):
            raise ProcedureExecutionValidationError(
                f"steps[{position}] must be a ProcedureStepRecord"
            )
        if step.step_index != position:
            raise ProcedureExecutionValidationError(
                f"steps must be ordered and contiguous from 0: "
                f"steps[{position}] has step_index {step.step_index}"
            )
        if step.started_at < started_at or step.ended_at > ended_at:
            raise ProcedureExecutionValidationError(
                f"steps[{position}] lies outside the run window"
            )
        if previous_started_at is not None and step.started_at < previous_started_at:
            raise ProcedureExecutionValidationError(
                f"steps[{position}] starts before the previous step"
            )
        previous_started_at = step.started_at
    return value


def _ensure_run_disposition_consistency(
    *,
    disposition: ProcedureRunDisposition,
    steps: tuple[ProcedureStepRecord, ...],
    control_evidence: tuple[ProcedureExecutionEvidence, ...],
) -> None:
    """Reject runs whose terminal control claim contradicts their own steps."""
    final = steps[-1] if steps else None

    if disposition is ProcedureRunDisposition.REACHED_END:
        if final is None:
            raise ProcedureExecutionValidationError(
                "a run cannot claim REACHED_END with no recorded steps"
            )
        if final.node_kind is not ExecutedNodeKind.END:
            raise ProcedureExecutionValidationError(
                "REACHED_END requires the final recorded step to be an END node"
            )
        if not control_evidence:
            raise ProcedureExecutionValidationError(
                "REACHED_END requires at least one explicit terminal control evidence reference"
            )
        return

    if disposition is ProcedureRunDisposition.HALTED_ON_STEP_FAILURE:
        if final is None or final.disposition not in _HALTING_DISPOSITIONS:
            raise ProcedureExecutionValidationError(
                "HALTED_ON_STEP_FAILURE requires a final step that failed, was denied, or timed out"
            )
        return

    if (
        disposition is ProcedureRunDisposition.DENIED
        and final is not None
        and final.disposition is not ProcedureStepDisposition.DENIED
    ):
        raise ProcedureExecutionValidationError(
            "a DENIED run must either record no steps or end on a DENIED step"
        )


def _ensure_task_verification_consistency(
    *,
    task_verification: ProcedureTaskVerification,
    task_verification_evidence: tuple[ProcedureExecutionEvidence, ...],
    task_id: TaskId | None,
) -> None:
    """Keep task-level verification separate, explicit, and fail-closed."""
    if task_verification is ProcedureTaskVerification.NOT_ASSESSED:
        if task_verification_evidence:
            raise ProcedureExecutionValidationError(
                "NOT_ASSESSED task verification must not carry task-verification evidence"
            )
        return
    if task_id is None:
        raise ProcedureExecutionValidationError(
            f"{task_verification.value} requires an explicit task_id"
        )
    if not task_verification_evidence:
        raise ProcedureExecutionValidationError(
            f"{task_verification.value} requires at least one explicit "
            "task-verification evidence reference"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRunRecord:
    """One immutable historical trace of a single procedure run.

    The record answers "what happened, and what evidence supports it?". It
    never answers "may AgentX do this again?" or "did the task succeed?".

    Identity is explicit: ``run_id`` names this run, ``(procedure_id,
    procedure_revision)`` names exactly which stored procedure revision (C2.03)
    was run, ``correlation_id`` ties the run into the canonical execution
    chain, and ``task_id`` links the originating task when there is one. Large
    canonical objects are never copied into the trace; only stable identities
    are referenced.

    Terminal control (``disposition``) and task verification
    (``task_verification``) are two separate fields on purpose. Reaching
    ``END`` never sets, implies, or upgrades task verification, and no method
    on this record derives a success boolean.
    """

    run_id: UUID
    procedure_id: ProcedureId
    procedure_revision: int
    correlation_id: UUID
    started_at: datetime
    ended_at: datetime
    disposition: ProcedureRunDisposition
    task_id: TaskId | None = None
    steps: tuple[ProcedureStepRecord, ...] = ()
    control_evidence: tuple[ProcedureExecutionEvidence, ...] = ()
    task_verification: ProcedureTaskVerification = ProcedureTaskVerification.NOT_ASSESSED
    task_verification_evidence: tuple[ProcedureExecutionEvidence, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = PROCEDURE_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        version = _ensure_integer(self.schema_version, field_name="schema_version")
        if version != PROCEDURE_EXECUTION_SCHEMA_VERSION:
            raise UnsupportedProcedureExecutionSchemaVersionError(
                f"unsupported procedure-execution schema version {version}; "
                f"supported version is {PROCEDURE_EXECUTION_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(self, "run_id", _ensure_uuid(self.run_id, field_name="run_id"))
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedureExecutionValidationError("procedure_id must be a ProcedureId")
        revision = _ensure_integer(self.procedure_revision, field_name="procedure_revision")
        if revision < 1:
            raise ProcedureExecutionValidationError(
                "procedure_revision must be a positive integer (first revision is 1)"
            )
        object.__setattr__(self, "procedure_revision", revision)
        object.__setattr__(
            self,
            "correlation_id",
            _ensure_uuid(self.correlation_id, field_name="correlation_id"),
        )
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise ProcedureExecutionValidationError("task_id must be a TaskId or None")
        started_at = _ensure_timestamp(self.started_at, field_name="started_at")
        ended_at = _ensure_timestamp(self.ended_at, field_name="ended_at")
        if ended_at < started_at:
            raise ProcedureExecutionValidationError("ended_at must not precede started_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        if not isinstance(self.disposition, ProcedureRunDisposition):
            raise ProcedureExecutionValidationError(
                "disposition must be a ProcedureRunDisposition member; "
                "this contract never infers a disposition from text"
            )
        if not isinstance(self.task_verification, ProcedureTaskVerification):
            raise ProcedureExecutionValidationError(
                "task_verification must be a ProcedureTaskVerification member"
            )
        object.__setattr__(
            self,
            "steps",
            _ensure_step_sequence(self.steps, started_at=started_at, ended_at=ended_at),
        )
        object.__setattr__(
            self,
            "control_evidence",
            _ensure_evidence_tuple(self.control_evidence, field_name="control_evidence"),
        )
        object.__setattr__(
            self,
            "task_verification_evidence",
            _ensure_evidence_tuple(
                self.task_verification_evidence, field_name="task_verification_evidence"
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_bounded_object(
                self.metadata,
                path="metadata",
                max_items=MAX_RUN_METADATA_ITEMS,
                max_bytes=MAX_RUN_METADATA_BYTES,
            ),
        )
        _ensure_run_disposition_consistency(
            disposition=self.disposition,
            steps=self.steps,
            control_evidence=self.control_evidence,
        )
        _ensure_task_verification_consistency(
            task_verification=self.task_verification,
            task_verification_evidence=self.task_verification_evidence,
            task_id=self.task_id,
        )

    def step_at(self, index: int) -> ProcedureStepRecord:
        """Return the step recorded at ``index``.

        Pure lookup over already-validated history. It replays nothing.
        """
        position = _ensure_integer(index, field_name="index")
        if position < 0 or position >= len(self.steps):
            raise ProcedureExecutionValidationError(f"no step recorded at index {position}")
        return self.steps[position]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "run_id": str(self.run_id),
            "procedure_id": self.procedure_id.to_str(),
            "procedure_revision": self.procedure_revision,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "started_at": _format_timestamp(self.started_at),
            "ended_at": _format_timestamp(self.ended_at),
            "steps": [step.to_dict() for step in self.steps],
            "disposition": self.disposition.value,
            "control_evidence": [item.to_dict() for item in self.control_evidence],
            "task_verification": self.task_verification.value,
            "task_verification_evidence": [
                item.to_dict() for item in self.task_verification_evidence
            ],
            "metadata": _to_json_object(self.metadata, path="metadata"),
        }

    def to_json(self) -> str:
        """Serialize this run to deterministic JSON text (sorted keys, no NaN)."""
        return _dump_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureRunRecord:
        """Strictly reconstruct one run record, failing closed.

        Deserialization restores persisted history verbatim — including hostile
        strings — without executing, dereferencing, promoting, or upgrading
        anything. A restored record is still inert data.
        """
        if "schema_version" not in raw:
            raise ProcedureExecutionDeserializationError(
                "procedure run missing required field: schema_version"
            )
        version_raw = raw["schema_version"]
        if isinstance(version_raw, bool) or not isinstance(version_raw, int):
            raise ProcedureExecutionDeserializationError("schema_version must be an integer")
        if version_raw != PROCEDURE_EXECUTION_SCHEMA_VERSION:
            raise UnsupportedProcedureExecutionSchemaVersionError(
                f"unsupported procedure-execution schema version {version_raw}; "
                f"supported version is {PROCEDURE_EXECUTION_SCHEMA_VERSION}"
            )
        _ensure_exact_fields(raw, expected=_RUN_FIELDS, record_name="procedure run")

        disposition_raw = raw["disposition"]
        if not isinstance(disposition_raw, str):
            raise ProcedureExecutionDeserializationError("disposition must be a string")
        try:
            disposition = ProcedureRunDisposition(disposition_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown run disposition: {disposition_raw!r}"
            ) from exc

        verification_raw = raw["task_verification"]
        if not isinstance(verification_raw, str):
            raise ProcedureExecutionDeserializationError("task_verification must be a string")
        try:
            task_verification = ProcedureTaskVerification(verification_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                f"unknown task verification: {verification_raw!r}"
            ) from exc

        procedure_raw = raw["procedure_id"]
        if not isinstance(procedure_raw, str):
            raise ProcedureExecutionDeserializationError("procedure_id must be a string")
        try:
            procedure_id = ProcedureId.parse(procedure_raw)
        except ValueError as exc:
            raise ProcedureExecutionDeserializationError(
                "procedure_id is not a valid ProcedureId"
            ) from exc

        task_raw = raw["task_id"]
        task_id: TaskId | None = None
        if task_raw is not None:
            if not isinstance(task_raw, str):
                raise ProcedureExecutionDeserializationError("task_id must be a string or null")
            try:
                task_id = TaskId.parse(task_raw)
            except ValueError as exc:
                raise ProcedureExecutionDeserializationError(
                    "task_id is not a valid TaskId"
                ) from exc

        steps_raw = raw["steps"]
        if not isinstance(steps_raw, list):
            raise ProcedureExecutionDeserializationError("steps must be a JSON array")
        if len(steps_raw) > MAX_PROCEDURE_RUN_STEPS:
            raise ProcedureExecutionDeserializationError(
                f"steps must contain at most {MAX_PROCEDURE_RUN_STEPS} records"
            )
        steps = tuple(
            ProcedureStepRecord.from_dict(_ensure_object(item, field_name=f"steps[{index}]"))
            for index, item in enumerate(steps_raw)
        )

        try:
            return cls(
                schema_version=version_raw,
                run_id=_parse_uuid(raw["run_id"], field_name="run_id"),
                procedure_id=procedure_id,
                procedure_revision=_ensure_integer(
                    raw["procedure_revision"], field_name="procedure_revision"
                ),
                task_id=task_id,
                correlation_id=_parse_uuid(raw["correlation_id"], field_name="correlation_id"),
                started_at=_parse_timestamp(raw["started_at"], field_name="started_at"),
                ended_at=_parse_timestamp(raw["ended_at"], field_name="ended_at"),
                steps=steps,
                disposition=disposition,
                control_evidence=_parse_evidence_tuple(
                    raw["control_evidence"], field_name="control_evidence"
                ),
                task_verification=task_verification,
                task_verification_evidence=_parse_evidence_tuple(
                    raw["task_verification_evidence"], field_name="task_verification_evidence"
                ),
                metadata=_ensure_object(raw["metadata"], field_name="metadata"),
            )
        except ProcedureExecutionDeserializationError:
            raise
        except ProcedureExecutionValidationError as exc:
            raise ProcedureExecutionDeserializationError(
                f"procedure run is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, raw: str) -> ProcedureRunRecord:
        """Deserialize deterministic JSON text into an inert run record."""
        return cls.from_dict(_decode_json_object(raw, record_name="procedure run"))
