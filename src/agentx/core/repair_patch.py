"""Canonical inert repair-patch *proposal* contract (M5.02).

Canonical C4.04 (:mod:`agentx.core.repair_candidates`) answers *"what category
of repair MAY be considered?"* and deliberately generates nothing. The next
repair stage needs an inert representation of exactly **one proposed change**.
This module owns that representation and nothing else.

What this module is:

    * :class:`RepairPatchKind` — the canonical, closed patch-proposal
      vocabulary. It has exactly one member,
      :attr:`RepairPatchKind.NODE_DEFINITION_REPLACEMENT`, because the only
      concrete repair family the landed canonical
      :class:`~agentx.core.repair_candidates.RepairCandidateKind` vocabulary
      can justify is ``NODE_DEFINITION_REVISION``.
    * :class:`RepairPatchProposal` — one immutable, deterministic,
      JSON-serializable record: the originating canonical
      :class:`~agentx.core.repair_candidates.RepairCandidate` (embedded by
      value), the exact target ``(procedure_id, revision, node_id)`` binding,
      the patch kind, a bounded inert JSON payload holding the proposed
      replacement node definition, and the caller-supplied instant the
      proposal was recorded.

This module defines the *proposal contract only*. There is deliberately:

    no generation algorithm · no model · no application · no validation of
    the payload against a Procedure Graph schema · no execution · no
    selection · no ranking · no scoring · no budget · no persistence · no
    migration.

A proposal is a *statement that a change was proposed*, never a verdict about
that change. In this contract:

    proposal != valid       proposal != safe        proposal != selected
    proposal != authorized  proposal != applied     proposal != verified
    proposal != active revision

The record therefore carries no field, property, method, or serialized key
named ``approved``, ``verified``, ``safe``, ``authorized``, ``applied``,
``selected``, ``valid``, ``active``, ``score``, ``rank``, or ``confidence``,
and payload keys that merely *spell* such words are inert payload data with
zero authority (see "Payload" below).

Core independence: ``agentx.core`` must not depend on ``agentx.procedures``.
This module therefore never imports ``ProcedureGraph``, ``ProcedureNode``,
``ProcedureNodeId``, any ACTION/branch node class, or the interpreter. The
proposed replacement definition is carried as a **bounded, JSON-compatible,
deep-frozen payload** that this module never parses, interprets, schema-checks,
compiles, imports, or executes. An outer composition layer that legitimately
owns both sides may later validate that payload against the canonical
Procedure Graph contracts; that validation is explicitly not performed here,
and its absence is exactly why a proposal is never "valid" in this module.

Target binding is exact and fails closed. A proposal names one canonical
:class:`~agentx.core.ids.ProcedureId`, one explicit positive integer
``target_revision``, and one graph-local ``target_node_id`` string, and each
must match the canonical subject carried by the embedded candidate's
diagnosis. There is no wildcard procedure id, no ``"*"``, no ``"latest"``, no
implicit "current" revision, and no fuzzy node lookup: a proposal made against
revision 3 can never silently be read as a proposal against revision 4.

Provenance is structural. The originating canonical ``RepairCandidate`` is
embedded by value and preserved untouched — and through it the exact C4.03
diagnosis, C4.02 ``PROCEDURE_NODE`` localization, and C4.01 classification. A
caller cannot claim a patch has structured justification without that chain: a
:attr:`~agentx.core.repair_candidates.RepairCandidateKind.UNKNOWN` candidate
names no repair family, so it can never justify a concrete node-definition
patch and is rejected. No proposer/author identity field exists: ``agentx.core``
owns no canonical agent/actor identity type, and a free-text ``proposed_by``
string would be an unverifiable, spoofable identity claim. Justification comes
from the candidate chain, not from who says so.

Security posture: the payload is **untrusted data**. A payload containing
``"permission=ADMIN"``, ``"risk=R0"``, ``"verified=true"``, ``"import os"``,
``"subprocess.run(...)"``, ``"clear emergency stop"``, SQL, PowerShell, a
tool-call JSON object, or a prompt injection has exactly zero authority: it is
stored, compared, and re-serialized as inert data. This module performs no
``eval``, ``exec``, ``compile``, ``__import__``, ``importlib``, ``pickle``,
``subprocess``, shell, filesystem, database, or network operation, and it
contains no code path that could interpret payload content as an instruction.
Authority belongs exclusively to ``agentx.kernel``.

Bounds: payload depth, per-collection size, total value count, string and key
length, integer magnitude, and total canonical-JSON size are all bounded, and
non-finite floats, ``bytes``, callables, sets, custom objects, and cyclic
object graphs are rejected. Nothing here is unbounded.

Immutability: nested payload structures are deep-frozen at construction
(mappings become read-only proxies, sequences become tuples), so mutating the
caller's original object after construction cannot mutate the proposal.

Determinism: the same typed inputs always produce byte-identical records and
byte-identical canonical JSON (UTC timestamps, sorted keys, exact fields,
explicit ``schema_version``, closed kind vocabulary, unknown-field rejection,
and no type coercion on decode).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.repair_candidates import (
    RepairCandidate,
    RepairCandidateDeserializationError,
    RepairCandidateKind,
)

__all__ = [
    "CANONICAL_REPAIR_PATCH_KINDS",
    "MAX_PATCH_PAYLOAD_COLLECTION_SIZE",
    "MAX_PATCH_PAYLOAD_DEPTH",
    "MAX_PATCH_PAYLOAD_INT_MAGNITUDE",
    "MAX_PATCH_PAYLOAD_JSON_BYTES",
    "MAX_PATCH_PAYLOAD_KEY_LENGTH",
    "MAX_PATCH_PAYLOAD_STRING_LENGTH",
    "MAX_PATCH_PAYLOAD_VALUES",
    "MAX_TARGET_NODE_ID_LENGTH",
    "REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION",
    "RepairPatchKind",
    "RepairPatchProposal",
    "RepairPatchProposalDeserializationError",
    "RepairPatchProposalValidationError",
    "UnsupportedRepairPatchProposalSchemaVersionError",
]

REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION: Final[int] = 1

#: Maximum nesting depth of the proposed payload (the payload object is depth 1).
MAX_PATCH_PAYLOAD_DEPTH: Final[int] = 8
#: Maximum number of entries in any single payload mapping or array.
MAX_PATCH_PAYLOAD_COLLECTION_SIZE: Final[int] = 256
#: Maximum total number of values (containers included) inside one payload.
MAX_PATCH_PAYLOAD_VALUES: Final[int] = 1_024
#: Maximum length of any payload string value.
MAX_PATCH_PAYLOAD_STRING_LENGTH: Final[int] = 4_096
#: Maximum length of any payload object key.
MAX_PATCH_PAYLOAD_KEY_LENGTH: Final[int] = 128
#: Maximum absolute value of any payload integer (bounded, JSON-portable).
MAX_PATCH_PAYLOAD_INT_MAGNITUDE: Final[int] = 2**53 - 1
#: Maximum size, in UTF-8 bytes, of the payload's canonical JSON encoding.
MAX_PATCH_PAYLOAD_JSON_BYTES: Final[int] = 65_536
#: Maximum length of the graph-local target node identity string.
MAX_TARGET_NODE_ID_LENGTH: Final[int] = 256

_PROPOSAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "kind",
        "candidate",
        "target_procedure_id",
        "target_revision",
        "target_node_id",
        "proposed_definition",
        "proposed_at",
    }
)


class RepairPatchProposalValidationError(ValueError):
    """Raised when repair-patch-proposal data violates the canonical contract."""


class RepairPatchProposalDeserializationError(RepairPatchProposalValidationError):
    """Raised when encoded repair-patch-proposal data cannot be decoded safely."""


class UnsupportedRepairPatchProposalSchemaVersionError(RepairPatchProposalDeserializationError):
    """Raised when encoded data uses an unsupported repair-patch-proposal schema."""


class RepairPatchKind(StrEnum):
    """The canonical, closed repair-patch-proposal vocabulary.

    The vocabulary is deliberately as small as the landed architecture
    justifies: the only concrete repair family canonical C4.04 can name is
    :attr:`~agentx.core.repair_candidates.RepairCandidateKind.NODE_DEFINITION_REVISION`,
    so exactly one patch kind exists. No member asserts that the proposed
    change is valid, safe, selected, authorized, applied, or verified.

    Deliberately absent (and why): there is no source-code patch, no textual
    diff/hunk, no shell or PowerShell patch, no Python patch, no kernel
    modification, no permission grant, no risk change, no budget change, no
    procedure activation/rollback, and no architecture change. None of those
    can be justified by a typed fact the canonical candidate/diagnosis chain
    carries, and each would smuggle authority or execution into a data record.

    Members:
        NODE_DEFINITION_REPLACEMENT: The proposal carries a complete inert
            replacement definition for exactly one localized procedure node of
            exactly one target procedure revision. "Replacement" describes the
            *shape of the proposal* (a whole node definition rather than a
            partial edit), never an act: nothing here replaces anything.
    """

    NODE_DEFINITION_REPLACEMENT = "node_definition_replacement"


#: The canonical patch kinds in their declared order. Exported so callers can
#: enumerate the closed vocabulary without importing the enum internals.
CANONICAL_REPAIR_PATCH_KINDS: Final[tuple[RepairPatchKind, ...]] = tuple(RepairPatchKind)

#: Patch kind -> the candidate kind that alone justifies it. ``UNKNOWN``
#: candidates justify no concrete patch kind and are absent by construction.
_REQUIRED_CANDIDATE_KIND: Final[Mapping[RepairPatchKind, RepairCandidateKind]] = MappingProxyType(
    {RepairPatchKind.NODE_DEFINITION_REPLACEMENT: RepairCandidateKind.NODE_DEFINITION_REVISION}
)


def _has_control_characters(value: str) -> bool:
    return any(character.isprintable() is False for character in value)


def _validate_kind(value: object) -> RepairPatchKind:
    if not isinstance(value, RepairPatchKind):
        raise RepairPatchProposalValidationError(
            "kind must be a RepairPatchKind member; this contract never infers "
            "a patch kind from text or payload content"
        )
    return value


def _validate_candidate(value: object) -> RepairCandidate:
    if not isinstance(value, RepairCandidate):
        raise RepairPatchProposalValidationError(
            "candidate must be a canonical RepairCandidate; a proposal may not claim "
            "structured justification without the candidate/diagnosis chain"
        )
    return value


def _validate_candidate_supports_kind(candidate: RepairCandidate, *, kind: RepairPatchKind) -> None:
    """Fail closed unless the candidate's kind justifies this patch kind."""
    if candidate.kind is RepairCandidateKind.UNKNOWN:
        raise RepairPatchProposalValidationError(
            "an UNKNOWN repair candidate names no repair family and therefore cannot "
            "justify a concrete node-definition patch proposal"
        )
    required = _REQUIRED_CANDIDATE_KIND[kind]
    if candidate.kind is not required:
        raise RepairPatchProposalValidationError(
            f"a {kind.value} proposal is justified only by a {required.value} repair "
            f"candidate; got {candidate.kind.value}"
        )


def _validate_target_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, ProcedureId):
        raise RepairPatchProposalValidationError(
            "target_procedure_id must be a canonical ProcedureId; wildcards, "
            '"*", and name lookups are not accepted'
        )
    return value


def _validate_target_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepairPatchProposalValidationError("target_revision must be an integer")
    if value < 1:
        raise RepairPatchProposalValidationError(
            "target_revision must be a positive integer naming one exact stored "
            "revision (first revision is 1); there is no implicit current or latest"
        )
    return value


def _validate_target_node_id(value: object) -> str:
    if not isinstance(value, str):
        raise RepairPatchProposalValidationError("target_node_id must be a string")
    if value == "" or value != value.strip():
        raise RepairPatchProposalValidationError("target_node_id must be non-empty and trimmed")
    if len(value) > MAX_TARGET_NODE_ID_LENGTH:
        raise RepairPatchProposalValidationError(
            f"target_node_id must be at most {MAX_TARGET_NODE_ID_LENGTH} characters"
        )
    if _has_control_characters(value):
        raise RepairPatchProposalValidationError(
            "target_node_id must not contain control characters"
        )
    return value


def _validate_target_binding(
    *,
    candidate: RepairCandidate,
    target_procedure_id: ProcedureId,
    target_node_id: str,
) -> None:
    """Bind the proposal to the exact canonical subject of its provenance.

    The embedded candidate's diagnosis always carries a canonical
    ``PROCEDURE_NODE`` localization, so both the procedure identity and the
    graph-local node identity of the subject are known exactly. A proposal
    that names a different procedure or node is rejected rather than
    reconciled: this contract never re-targets, resolves, or guesses.
    """
    diagnosis = candidate.diagnosis
    if target_procedure_id != diagnosis.procedure_id:
        raise RepairPatchProposalValidationError(
            "target_procedure_id must equal the procedure identity of the embedded "
            "candidate's diagnosis; a proposal is never re-targeted"
        )
    if target_node_id != diagnosis.procedure_node_id:
        raise RepairPatchProposalValidationError(
            "target_node_id must equal the localized procedure node identity of the "
            "embedded candidate's diagnosis; no fuzzy node lookup is performed"
        )


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RepairPatchProposalValidationError("proposed_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepairPatchProposalValidationError("proposed_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RepairPatchProposalDeserializationError("proposed_at must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RepairPatchProposalDeserializationError(
            "proposed_at is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RepairPatchProposalDeserializationError("proposed_at must be timezone-aware")
    return parsed.astimezone(UTC)


class _PayloadBudget:
    """Mutable counter bounding the total number of values in one payload."""

    __slots__ = ("remaining",)

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self, *, path: str) -> None:
        if self.remaining <= 0:
            raise RepairPatchProposalValidationError(
                f"{path} exceeds the maximum payload size of {MAX_PATCH_PAYLOAD_VALUES} values"
            )
        self.remaining -= 1


def _validate_payload_key(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        raise RepairPatchProposalValidationError(f"{path} contains a non-string object key")
    if value == "":
        raise RepairPatchProposalValidationError(f"{path} contains an empty object key")
    if len(value) > MAX_PATCH_PAYLOAD_KEY_LENGTH:
        raise RepairPatchProposalValidationError(
            f"{path} contains an object key longer than {MAX_PATCH_PAYLOAD_KEY_LENGTH} characters"
        )
    if _has_control_characters(value):
        raise RepairPatchProposalValidationError(
            f"{path} contains an object key with control characters"
        )
    return value


def _freeze_payload_value(
    value: object, *, path: str, depth: int, budget: _PayloadBudget
) -> object:
    """Validate one payload value and return a deep-frozen inert copy.

    The value is treated as untrusted data throughout: it is never parsed as a
    procedure definition, never schema-checked, never compiled, never imported,
    and never executed. Only JSON-compatible primitives and containers survive;
    everything else (``bytes``, callables, sets, ``Decimal``, arbitrary
    objects, non-finite floats) is rejected. Cyclic object graphs terminate on
    the depth bound rather than recursing forever.
    """
    budget.spend(path=path)
    if depth > MAX_PATCH_PAYLOAD_DEPTH:
        raise RepairPatchProposalValidationError(
            f"{path} exceeds the maximum payload nesting depth of {MAX_PATCH_PAYLOAD_DEPTH}"
        )
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_PATCH_PAYLOAD_INT_MAGNITUDE:
            raise RepairPatchProposalValidationError(
                f"{path} contains an integer outside the bounded payload range"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RepairPatchProposalValidationError(
                f"{path} contains a non-finite float (NaN and Infinity are not JSON values)"
            )
        return value
    if isinstance(value, str):
        if len(value) > MAX_PATCH_PAYLOAD_STRING_LENGTH:
            raise RepairPatchProposalValidationError(
                f"{path} contains a string longer than {MAX_PATCH_PAYLOAD_STRING_LENGTH} characters"
            )
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        raise RepairPatchProposalValidationError(
            f"{path} contains binary data; the payload must be JSON-compatible"
        )
    if isinstance(value, Mapping):
        if len(value) > MAX_PATCH_PAYLOAD_COLLECTION_SIZE:
            raise RepairPatchProposalValidationError(
                f"{path} contains more than {MAX_PATCH_PAYLOAD_COLLECTION_SIZE} entries"
            )
        frozen: dict[str, object] = {}
        for raw_key, item in value.items():
            key = _validate_payload_key(raw_key, path=path)
            frozen[key] = _freeze_payload_value(
                item, path=f"{path}.{key}", depth=depth + 1, budget=budget
            )
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        if len(value) > MAX_PATCH_PAYLOAD_COLLECTION_SIZE:
            raise RepairPatchProposalValidationError(
                f"{path} contains more than {MAX_PATCH_PAYLOAD_COLLECTION_SIZE} items"
            )
        return tuple(
            _freeze_payload_value(item, path=f"{path}[{index}]", depth=depth + 1, budget=budget)
            for index, item in enumerate(value)
        )
    raise RepairPatchProposalValidationError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_value(value: object, *, path: str) -> object:
    """Convert a frozen payload value back to plain JSON-compatible data."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # pragma: no cover - construction rejects this
                raise RepairPatchProposalValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise RepairPatchProposalValidationError(  # pragma: no cover - defensive
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_proposed_definition(value: object) -> Mapping[str, object]:
    """Validate, bound, and deep-freeze the proposed replacement definition.

    The result is an inert read-only structure. This module assigns it no
    meaning: whether the definition is a well-formed procedure node, whether
    it is safe, and whether it should ever be applied are questions for
    contracts that are deliberately not consulted here.
    """
    if not isinstance(value, Mapping):
        raise RepairPatchProposalValidationError(
            "proposed_definition must be a JSON object describing the proposed "
            "replacement node definition"
        )
    if not value:
        raise RepairPatchProposalValidationError(
            "proposed_definition must not be empty; a proposal that proposes nothing "
            "is not a proposal"
        )
    budget = _PayloadBudget(MAX_PATCH_PAYLOAD_VALUES)
    frozen = _freeze_payload_value(value, path="proposed_definition", depth=1, budget=budget)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("payload freezing produced a non-mapping")
    encoded = _canonical_json(_to_json_value(frozen, path="proposed_definition"))
    if len(encoded.encode("utf-8")) > MAX_PATCH_PAYLOAD_JSON_BYTES:
        raise RepairPatchProposalValidationError(
            f"proposed_definition exceeds the maximum encoded payload size of "
            f"{MAX_PATCH_PAYLOAD_JSON_BYTES} bytes"
        )
    return frozen


def _require_exact_fields(raw: Mapping[str, object]) -> None:
    actual = set(raw)
    missing = _PROPOSAL_FIELDS - actual
    unknown = actual - _PROPOSAL_FIELDS
    if missing:
        raise RepairPatchProposalDeserializationError(
            f"repair patch proposal missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise RepairPatchProposalDeserializationError(
            f"repair patch proposal contains unknown fields: {sorted(unknown)}"
        )


def _parse_kind(value: object) -> RepairPatchKind:
    """Decode exactly one canonical patch kind and fail closed otherwise.

    Unrecognized input is rejected outright; it is never coerced, mapped onto
    a "closest" member, or downgraded, because accepting foreign vocabulary
    would fabricate a proposal the data never contained.
    """
    if not isinstance(value, str):
        raise RepairPatchProposalDeserializationError("kind must be a string")
    try:
        return RepairPatchKind(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_REPAIR_PATCH_KINDS)
        raise RepairPatchProposalDeserializationError(f"kind must be one of {values}") from exc


def _parse_candidate(value: object) -> RepairCandidate:
    if not isinstance(value, Mapping):
        raise RepairPatchProposalDeserializationError("candidate must be a JSON object")
    try:
        return RepairCandidate.from_dict(value)
    except RepairCandidateDeserializationError as exc:
        raise RepairPatchProposalDeserializationError(f"candidate is invalid: {exc}") from exc


def _reject_json_constant(value: str) -> object:
    """Reject the JSON extensions ``NaN``/``Infinity``/``-Infinity`` on decode."""
    raise RepairPatchProposalDeserializationError(
        f"repair patch proposal JSON contains the non-finite constant {value}"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of silently keeping the last one."""
    decoded: dict[str, object] = {}
    for key, item in pairs:
        if key in decoded:
            raise RepairPatchProposalDeserializationError(
                f"repair patch proposal JSON contains a duplicate object key: {key!r}"
            )
        decoded[key] = item
    return decoded


def _parse_target_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, str):
        raise RepairPatchProposalDeserializationError("target_procedure_id must be a string")
    try:
        return ProcedureId.parse(value)
    except ValueError as exc:
        raise RepairPatchProposalDeserializationError(
            "target_procedure_id is not a valid ProcedureId"
        ) from exc


def _parse_proposed_definition(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RepairPatchProposalDeserializationError("proposed_definition must be a JSON object")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairPatchProposal:
    """One immutable, inert statement that a specific change was *proposed*.

    The record is pure data. Constructing, comparing, serializing, or decoding
    it applies nothing, mutates no procedure, creates no revision, touches no
    store, grants no authority, changes no risk/budget/stop state, invokes no
    model, and claims no verification. Hostile content can only ever reach the
    record inside ``proposed_definition`` (or inside the already-canonical
    embedded candidate), where it stays exactly what it was: inert data.

    A proposal is a candidate change, never a verdict about that change:

        proposal != valid · proposal != safe · proposal != selected ·
        proposal != authorized · proposal != applied · proposal != verified ·
        proposal != active revision

    Attributes:
        kind: The canonical :class:`RepairPatchKind`. Must be supplied as a
            typed enum member; this contract never infers it from text.
        candidate: The exact canonical C4.04
            :class:`~agentx.core.repair_candidates.RepairCandidate` this
            proposal arose from, embedded by value and preserved untouched —
            the full provenance chain to the diagnosis, localization, and
            classification. An ``UNKNOWN`` candidate is rejected.
        target_procedure_id: The exact canonical
            :class:`~agentx.core.ids.ProcedureId` this proposal targets. Must
            equal the procedure identity of the embedded candidate's
            diagnosis.
        target_revision: The exact stored procedure revision this proposal was
            written against (positive integer; first revision is 1). It is a
            binding, not an instruction, and it never resolves to "latest".
        target_node_id: The graph-local identity string of the procedure node
            the proposal replaces. Must equal the localized node identity of
            the embedded candidate's diagnosis. (The canonical
            ``ProcedureNodeId`` type lives outward in ``agentx.procedures``
            and must not be imported into ``agentx.core``.)
        proposed_definition: The bounded, deep-frozen, JSON-compatible
            replacement node definition. Inert data; never parsed, validated,
            compiled, or executed here.
        proposed_at: Caller-supplied timezone-aware instant the proposal was
            recorded, normalized to UTC. A record-keeping timestamp only: it
            authorizes nothing, schedules nothing, and expires nothing.
        schema_version: Canonical serialization schema version.
    """

    kind: RepairPatchKind
    candidate: RepairCandidate
    target_procedure_id: ProcedureId
    target_revision: int
    target_node_id: str
    proposed_definition: Mapping[str, object]
    proposed_at: datetime
    schema_version: int = REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        kind = _validate_kind(self.kind)
        candidate = _validate_candidate(self.candidate)
        _validate_candidate_supports_kind(candidate, kind=kind)
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise RepairPatchProposalValidationError("schema_version must be an integer")
        if self.schema_version != REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION:
            raise RepairPatchProposalValidationError(
                f"schema_version must be {REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION}"
            )
        target_procedure_id = _validate_target_procedure_id(self.target_procedure_id)
        target_node_id = _validate_target_node_id(self.target_node_id)
        _validate_target_binding(
            candidate=candidate,
            target_procedure_id=target_procedure_id,
            target_node_id=target_node_id,
        )
        object.__setattr__(self, "target_procedure_id", target_procedure_id)
        object.__setattr__(self, "target_node_id", target_node_id)
        object.__setattr__(self, "target_revision", _validate_target_revision(self.target_revision))
        object.__setattr__(
            self, "proposed_definition", _validate_proposed_definition(self.proposed_definition)
        )
        object.__setattr__(self, "proposed_at", _validate_timestamp(self.proposed_at))

    @property
    def target(self) -> tuple[ProcedureId, int, str]:
        """The exact ``(procedure_id, revision, node_id)`` binding of this proposal.

        A convenience view of data already stored on the record. It resolves
        nothing, looks nothing up, and never widens to another revision.
        """
        return (self.target_procedure_id, self.target_revision, self.target_node_id)

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "candidate": self.candidate.to_dict(),
            "target_procedure_id": self.target_procedure_id.to_str(),
            "target_revision": self.target_revision,
            "target_node_id": self.target_node_id,
            "proposed_definition": _to_json_value(
                self.proposed_definition, path="proposed_definition"
            ),
            "proposed_at": _format_timestamp(self.proposed_at),
        }

    def to_json(self) -> str:
        """Serialize deterministically without object hooks or executable types."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RepairPatchProposal:
        """Validate and reconstruct one canonical proposal, failing closed."""
        if not isinstance(raw, Mapping):
            raise RepairPatchProposalDeserializationError(
                "repair patch proposal must be a JSON object"
            )
        if "schema_version" not in raw:
            raise RepairPatchProposalDeserializationError(
                "repair patch proposal missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise RepairPatchProposalDeserializationError("schema_version must be an integer")
        if version != REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION:
            raise UnsupportedRepairPatchProposalSchemaVersionError(
                f"unsupported repair patch proposal schema version {version}; "
                f"supported version is {REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw)

        revision = raw["target_revision"]
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise RepairPatchProposalDeserializationError("target_revision must be an integer")
        node_id = raw["target_node_id"]
        if not isinstance(node_id, str):
            raise RepairPatchProposalDeserializationError("target_node_id must be a string")

        try:
            return cls(
                schema_version=version,
                kind=_parse_kind(raw["kind"]),
                candidate=_parse_candidate(raw["candidate"]),
                target_procedure_id=_parse_target_procedure_id(raw["target_procedure_id"]),
                target_revision=revision,
                target_node_id=node_id,
                proposed_definition=_parse_proposed_definition(raw["proposed_definition"]),
                proposed_at=_parse_timestamp(raw["proposed_at"]),
            )
        except RepairPatchProposalDeserializationError:
            raise
        except RepairPatchProposalValidationError as exc:
            raise RepairPatchProposalDeserializationError(
                f"repair patch proposal is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> RepairPatchProposal:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise RepairPatchProposalDeserializationError("repair patch proposal JSON must be text")
        try:
            decoded = json.loads(
                text,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except RepairPatchProposalDeserializationError:
            raise
        except ValueError as exc:
            raise RepairPatchProposalDeserializationError(
                "repair patch proposal JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise RepairPatchProposalDeserializationError(
                "repair patch proposal JSON root must be an object"
            )
        return cls.from_dict(decoded)
