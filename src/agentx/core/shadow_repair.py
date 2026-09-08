"""Canonical inert shadow-repair execution-evidence contract (C4.07).

Before AgentX replaces a working procedure revision with a repaired
revision, it needs evidence that the repaired candidate was TRIED in a way
that does not commit its external effects as the live canonical procedure
state. This module owns the DATA MODEL for exactly that evidence: one
immutable record of one *shadow repair trial*, supplied by a future
controlled shadow runner (a later composition task).

C4.07 answers: *What is the canonical, immutable shape of the evidence one
shadow repair trial must produce, and which internally contradictory
combinations are structurally impossible?*

It does NOT answer: *Did the candidate actually pass in a safe sandbox?*
Nothing in this module executes, sandboxes, patches, or decides. It is the
record a future runner must fill — and the contradictions a filled record
cannot contain.

What "shadow" means here (and only this):

    the candidate/repaired procedure was evaluated WITHOUT committing its
    external effects as the live canonical procedure state.

That claim is recorded as typed data (``mode == NON_COMMITTING`` plus the
per-step side-effect observations). It is a CLAIM carried by the record,
supplied by the runner. This contract does NOT guarantee OS-level
sandboxing, process isolation, or any security boundary, and it must never
be read as one. No member of the mode vocabulary claims stronger isolation
than "non-committing", because the architecture supports no stronger
structured fact today.

What this module is:

    * :class:`ShadowRepairMode` — the closed vocabulary of how the trial
      was evaluated relative to canonical live state (currently exactly one
      member: :attr:`ShadowRepairMode.NON_COMMITTING`).
    * :class:`ShadowRepairDisposition` — the closed trial-outcome
      vocabulary: ``PASSED``, ``FAILED``, ``ABORTED``, ``UNSAFE_TO_EVALUATE``,
      ``INSUFFICIENT_EVIDENCE``.
    * :class:`ShadowStepOutcome` — the closed per-step vocabulary
      (``OBSERVED`` / ``FAILED``).
    * :class:`ShadowRevisionOutcome` — the closed per-revision comparison
      vocabulary (``PASSED`` / ``FAILED``) for the ORIGINAL revision's
      behavior on the trial's test objective, where the runner recorded one.
    * :class:`ShadowRepairStepEvidence` — one ordered, bounded, typed stage
      of the trial: where it happened (node), what was observed, whether an
      unexpected external effect appeared, and whether that effect was
      modeled as contained/reverted.
    * :class:`ShadowRepairResult` — one immutable shadow repair trial: the
      exact run/procedure/revision/correlation/scope binding, the time
      window, the mode, the disposition, the explicit canonical-style
      verification evidence, the ordered steps, and the inert comparison
      facts.
    * :data:`ShadowRepairRunId` — the run identity type. Deliberately a
      plain non-nil ``UUID``: the canonical ``agentx.core.ids`` domain-ID
      convention is not extended here, and this contract introduces no
      competing identifier types.

Shadow semantics (structural, not aspirational):

    shadow != sandboxed        shadow != isolated
    shadow != safe             shadow != executed-elsewhere

    PASSED != task succeeded   PASSED != candidate applied
    PASSED != revision replaced PASSED != no exception occurred

A :attr:`ShadowRepairDisposition.PASSED` record is the claim that the
candidate met the trial's explicit test requirement under canonical-style
verification. It is NOT a claim that the original task now succeeds, that
the candidate has been applied anywhere, or that any live revision was
touched. Replacement policy is owned by later tasks (C4.08); this record
cannot perform it, and carries no field, method, or serialized key that
could.

Verification semantics (fail closed):

    * ``PASSED`` requires explicit canonical-style verification evidence —
      a :class:`~agentx.core.events.VerificationPayload` (the same core
      C1.02 payload A1.10 emits for canonical events, reused inward so
      ``agentx.core`` stays independent of the outward Capability ABI) with
      ``passed == True`` — plus at least one recorded step, no step with a
      :attr:`ShadowStepOutcome.FAILED` outcome, and no step observing an
      uncontained external effect.
    * "No exception", a procedure reaching END, an execute() return, the
      presence of an observation, candidate text, a model claim, or a
      string reading ``"passed=true"`` is NOT verification. None of those
      can construct, promote, or imply a ``PASSED`` disposition here.
    * ``FAILED`` requires an explicit failure signal: an explicit failing
      verification, or at least one step whose evaluation explicitly failed.
      A record cannot be ``FAILED`` while carrying a PASSING verification.
    * ``ABORTED``, ``UNSAFE_TO_EVALUATE``, and ``INSUFFICIENT_EVIDENCE``
      must not carry verification evidence at all: an aborted trial, a
      refused trial, and an evidence-poor trial each claim no canonical
      verdict.

Side-effect semantics (no silent contradiction):

    * Each step may record ``external_effect_observed`` (an unexpected
      external effect was observed during that stage) and
      ``effect_contained`` (that observed effect was explicitly modeled as
      contained/reverted).
    * Containment may only be claimed for an OBSERVED effect:
      ``effect_contained=True`` with ``external_effect_observed=False`` is
      rejected.
    * A ``PASSED`` disposition is structurally impossible while any step
      records an observed, UNCONTAINED external effect. A trial claiming
      PASSED while also recording uncontrolled external mutation cannot be
      constructed; the contradiction is rejected at validation time, not
      accepted and annotated.

Comparison semantics (evidence, never a verdict):

    * ``original_outcome`` records the SOURCE revision's observed behavior
      on the trial's test objective when the runner recorded one (``None``
      means "not evaluated in this trial"). It is historical evidence, not
      authority, and it is never re-derived or cross-checked against the
      candidate's disposition.
    * ``regression_node_ids`` records the bounded set of procedure nodes
      for which the runner observed the candidate behaving WORSE than the
      source revision. It is evidence of where regressions were seen.
    * Nothing in this contract declares the candidate superior, better,
      safer, or replaceable. A ``PASSED`` candidate with regression
      indicators is a perfectly valid record. A later policy decides
      replacement; this record only preserves what was observed.

Identity and binding (no "latest", no wildcards):

    * Every result binds EXACTLY: one non-nil run UUID, one canonical
      :class:`~agentx.core.ids.ProcedureId`, one explicit positive source
      revision, exactly one candidate identity (an explicit positive
      candidate revision number, OR an opaque non-empty candidate
      fingerprint — never both, never neither, never the same revision as
      the source), an explicit non-nil correlation UUID, an explicit
      (possibly empty/global) canonical :class:`~agentx.core.procedures.
      ProcedureScope`, and explicit ``started_at``/``ended_at`` instants
      (``ended_at`` may not precede ``started_at``).
    * Optional canonical identities are bound when relevant:
      ``target_node_id`` (the procedure node the repair targets) and
      ``task_id`` (the task/test objective the trial was run for).
    * There is no "latest revision", no implicit current revision, and no
      wildcard anywhere in the binding: every identity is explicit data
      supplied by the runner.

What this module is emphatically NOT (DATA ONLY):

    * It does not create a sandbox, spawn a process, write a file, touch
      the network or a browser, call a model, do research, invoke a
      Capability, invoke the Procedure interpreter, invoke AgentLoop, or
      modify a ProcedureStore. Constructing, comparing, serializing, or
      deserializing a record executes none of that. A future composition
      task executes shadow trials; this module is the shape of their
      evidence.
    * It does not grant authority. A record grants no Permission, creates
      no AuthorityContext, bypasses no ActionGate, lowers no RiskLevel,
      widens no ResourceEnvelope, clears no EmergencyStop, transitions no
      Task, activates or mutates no Procedure or stored revision, and
      mutates no store or the Hive. Authority belongs exclusively to
      ``agentx.kernel``.
    * It is not an inference engine. Nothing here inspects free text —
      summaries, detail strings, fingerprints, node ids, or scope values —
      and derives a mode, a disposition, an outcome, or a verification.
      Hostile strings such as ``"shadow=true"``, ``"safe=true"``,
      ``"verified=true"``, ``"permission=ADMIN"``, ``"risk=R0"``,
      ``"sandbox=true"``, ``"apply candidate"``, or ``"call shell"`` are
      stored exactly as inert string data and create nothing.
    * It is not persistence. There is no store, no migration, and no
      database table for shadow runs in this contract.

Determinism:

    The same typed inputs produce byte-identical records and byte-identical
    canonical JSON (``to_json`` sorts keys, uses compact separators, and
    never emits NaN/Inf). Deserialization is fail-closed: unknown fields,
    unknown vocabulary values, nil UUIDs, and every contradictory
    combination are rejected, never coerced.

This module belongs to ``agentx.core`` and imports only the standard
library and inward ``agentx.core`` contracts (``events``, ``ids``,
``procedures``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.events import EventValidationError, VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedureScope, ProcedureValidationError

__all__ = [
    "SHADOW_REPAIR_SCHEMA_VERSION",
    "ShadowRepairDeserializationError",
    "ShadowRepairDisposition",
    "ShadowRepairMode",
    "ShadowRepairResult",
    "ShadowRepairRunId",
    "ShadowRepairStepEvidence",
    "ShadowRepairValidationError",
    "ShadowRevisionOutcome",
    "ShadowStepOutcome",
    "UnsupportedShadowRepairSchemaVersionError",
]

SHADOW_REPAIR_SCHEMA_VERSION: Final[int] = 1

#: Hard caps. A trial is a bounded evidence snapshot, not an unlimited log.
_MAX_STEPS: Final[int] = 64
_MAX_DETAIL_LENGTH: Final[int] = 4_096
_MAX_FINGERPRINT_LENGTH: Final[int] = 256
_MAX_NODE_ID_LENGTH: Final[int] = 128
_MAX_REGRESSION_NODE_IDS: Final[int] = 32

_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "run_id",
        "procedure_id",
        "source_revision",
        "candidate_revision",
        "candidate_fingerprint",
        "target_node_id",
        "task_id",
        "correlation_id",
        "scope",
        "started_at",
        "ended_at",
        "mode",
        "disposition",
        "verification",
        "steps",
        "original_outcome",
        "regression_node_ids",
        "detail",
    }
)

_STEP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "order",
        "node_id",
        "outcome",
        "external_effect_observed",
        "effect_contained",
    }
)


class ShadowRepairValidationError(ValueError):
    """Raised when shadow-repair evidence violates the canonical contract."""


class ShadowRepairDeserializationError(ShadowRepairValidationError):
    """Raised when encoded shadow-repair evidence cannot be decoded safely."""


class UnsupportedShadowRepairSchemaVersionError(ShadowRepairDeserializationError):
    """Raised when encoded evidence uses a schema version this code cannot read."""


#: Canonical identity of one shadow repair trial.
#:
#: Deliberately a plain non-nil ``UUID`` rather than a new
#: ``agentx.core.ids.DomainId`` subclass: the existing UUID conventions
#: (non-nil UUID, canonical lowercase string serialization) are sufficient
#: for a run identity that is never resolved by a store this contract owns,
#: and this module introduces no competing identifier types.
ShadowRepairRunId = UUID


class ShadowRepairMode(StrEnum):
    """Closed vocabulary of how a shadow trial was evaluated.

    The vocabulary is deliberately minimal: the architecture supports
    exactly one shadow property today — that the candidate's external
    effects were NOT committed as the live canonical procedure state.

    Members:
        NON_COMMITTING: The trial's external effects were not committed as
            the live canonical procedure state. This records a typed claim
            supplied by the runner. It is NOT a claim of OS-level
            sandboxing, process isolation, or any security boundary; no
            member of this vocabulary overclaims isolation.
    """

    NON_COMMITTING = "non_committing"


class ShadowRepairDisposition(StrEnum):
    """Closed outcome vocabulary for one shadow repair trial.

    A disposition is an explicit typed claim about how the trial ended,
    validated against the record's own evidence (fail closed):

        - ``PASSED`` requires explicit passing canonical-style
          verification, at least one recorded step, no failed step, and no
          uncontained external effect.
        - ``FAILED`` requires an explicit failure signal (a failing
          verification or a failed step) and cannot coexist with a passing
          verification.
        - ``ABORTED``, ``UNSAFE_TO_EVALUATE``, and ``INSUFFICIENT_EVIDENCE``
          must carry no verification evidence at all.

    No disposition here means "the original task now succeeds", "the
    candidate was applied", or "the revision was replaced".
    """

    PASSED = "passed"
    FAILED = "failed"
    ABORTED = "aborted"
    UNSAFE_TO_EVALUATE = "unsafe_to_evaluate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ShadowStepOutcome(StrEnum):
    """Closed per-step vocabulary for one stage of a shadow trial.

    Members:
        OBSERVED: The stage was evaluated and an observation was recorded;
            no typed failure is claimed for this stage. An observation is
            evidence of what was seen — it is NOT verification and never
            implies success.
        FAILED: The candidate's evaluation at this stage explicitly failed.
            A trial with a failed step cannot be ``PASSED``.
    """

    OBSERVED = "observed"
    FAILED = "failed"


class ShadowRevisionOutcome(StrEnum):
    """Closed per-revision vocabulary for the comparison side of a trial.

    Records the observed behavior of one revision (the ORIGINAL/source
    revision in this contract) on the trial's test objective. This is
    historical evidence: it is never re-derived, never authority, and never
    a claim that either revision is superior.

    Members:
        PASSED: The revision met the test objective when evaluated.
        FAILED: The revision did not meet the test objective when evaluated.
    """

    PASSED = "passed"
    FAILED = "failed"


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ShadowRepairValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ShadowRepairValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_nonempty_trimmed_bounded(value: object, *, field_name: str, max_length: int) -> str:
    text = _validate_nonempty_trimmed(value, field_name=field_name)
    if len(text) > max_length:
        raise ShadowRepairValidationError(f"{field_name} must be at most {max_length} characters")
    return text


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ShadowRepairValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ShadowRepairValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _validate_uuid(value, field_name=field_name)


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise ShadowRepairValidationError(
            f"task_id must be a TaskId or None, got {type(value).__name__}"
        )
    return value


def _validate_revision(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ShadowRepairValidationError(f"{field_name} must be a positive integer")
    if value < 1:
        raise ShadowRepairValidationError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_optional_revision(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_revision(value, field_name=field_name)


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ShadowRepairValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ShadowRepairValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            f"{field_name} must be a valid ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShadowRepairDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(f"{field_name} is not a valid UUID") from exc
    if parsed.int == 0:
        raise ShadowRepairDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _parse_uuid(value, field_name=field_name)


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError("task_id is not a valid TaskId") from exc


def _parse_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("procedure_id must be a UUID string")
    try:
        return ProcedureId.parse(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            "procedure_id is not a valid non-nil ProcedureId"
        ) from exc


def _parse_mode(value: object) -> ShadowRepairMode:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("mode must be a string")
    try:
        return ShadowRepairMode(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            f"mode must be one of {[member.value for member in ShadowRepairMode]}"
        ) from exc


def _parse_disposition(value: object) -> ShadowRepairDisposition:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("disposition must be a string")
    try:
        return ShadowRepairDisposition(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            f"disposition must be one of {[member.value for member in ShadowRepairDisposition]}"
        ) from exc


def _parse_step_outcome(value: object) -> ShadowStepOutcome:
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("step outcome must be a string")
    try:
        return ShadowStepOutcome(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            f"step outcome must be one of {[member.value for member in ShadowStepOutcome]}"
        ) from exc


def _parse_revision_outcome(value: object) -> ShadowRevisionOutcome | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ShadowRepairDeserializationError("original_outcome must be a string or null")
    try:
        return ShadowRevisionOutcome(value)
    except ValueError as exc:
        raise ShadowRepairDeserializationError(
            f"original_outcome must be one of "
            f"{[member.value for member in ShadowRevisionOutcome]} or null"
        ) from exc


def _parse_verification(value: object) -> VerificationPayload | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ShadowRepairDeserializationError("verification must be a JSON object or null")
    try:
        return VerificationPayload.from_dict(value)
    except EventValidationError as exc:
        raise ShadowRepairDeserializationError(
            f"verification is not a valid canonical verification payload: {exc}"
        ) from exc


def _parse_scope(value: object) -> ProcedureScope:
    if not isinstance(value, Mapping):
        raise ShadowRepairDeserializationError("scope must be a JSON object")
    try:
        return ProcedureScope.from_dict(value)
    except ProcedureValidationError as exc:
        raise ShadowRepairDeserializationError(
            f"scope is not a valid ProcedureScope: {exc}"
        ) from exc


def _validate_node_id(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_nonempty_trimmed_bounded(
        value, field_name=field_name, max_length=_MAX_NODE_ID_LENGTH
    )


def _validate_node_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ShadowRepairValidationError("regression_node_ids must be a tuple of node ids")
    if len(value) > _MAX_REGRESSION_NODE_IDS:
        raise ShadowRepairValidationError(
            f"regression_node_ids must contain at most {_MAX_REGRESSION_NODE_IDS} entries"
        )
    seen: set[str] = set()
    for item in value:
        node_id = _validate_nonempty_trimmed_bounded(
            item, field_name="regression_node_ids entry", max_length=_MAX_NODE_ID_LENGTH
        )
        if node_id in seen:
            raise ShadowRepairValidationError(
                f"regression_node_ids must not contain duplicates: {node_id!r}"
            )
        seen.add(node_id)
    return value


def _validate_detail(value: object) -> str | None:
    if value is None:
        return None
    return _validate_nonempty_trimmed_bounded(
        value, field_name="detail", max_length=_MAX_DETAIL_LENGTH
    )


def _validate_steps(value: object) -> tuple[ShadowRepairStepEvidence, ...]:
    if not isinstance(value, tuple):
        raise ShadowRepairValidationError("steps must be a tuple of step evidence")
    if len(value) > _MAX_STEPS:
        raise ShadowRepairValidationError(f"steps must contain at most {_MAX_STEPS} entries")
    for item in value:
        if not isinstance(item, ShadowRepairStepEvidence):
            raise ShadowRepairValidationError(
                f"steps must contain ShadowRepairStepEvidence, got {type(item).__name__}"
            )
    orders = [step.order for step in value]
    if len(set(orders)) != len(orders):
        raise ShadowRepairValidationError("steps must not contain duplicate order values")
    if orders != list(range(1, len(orders) + 1)):
        raise ShadowRepairValidationError(
            "steps must be exactly the contiguous sequence 1..N; an out-of-order, "
            "gapped, or unnumbered step sequence is invalid"
        )
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowRepairStepEvidence:
    """One ordered, typed stage of one shadow repair trial.

    A step is inert evidence about ONE stage: where it happened
    (``node_id``, when relevant), what was recorded (``outcome``), whether
    an unexpected external effect was observed
    (``external_effect_observed``), and whether that observed effect was
    explicitly modeled as contained/reverted (``effect_contained``).

    A step with ``outcome == OBSERVED`` is evidence that a stage was
    evaluated and an observation was recorded. It is NOT verification and
    never implies success. A step with ``outcome == FAILED`` is an explicit
    typed failure for that stage and makes a ``PASSED`` trial impossible.

    Containment is a claim about an OBSERVED effect only:
    ``effect_contained=True`` with ``external_effect_observed=False`` is
    rejected, because containment without an observed effect is a
    fabrication.
    """

    order: int
    node_id: str | None = None
    outcome: ShadowStepOutcome = ShadowStepOutcome.OBSERVED
    external_effect_observed: bool = False
    effect_contained: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.order, int) or isinstance(self.order, bool):
            raise ShadowRepairValidationError("order must be a positive integer")
        if self.order < 1:
            raise ShadowRepairValidationError("order must be a positive integer (first is 1)")
        object.__setattr__(self, "node_id", _validate_node_id(self.node_id, field_name="node_id"))
        if not isinstance(self.outcome, ShadowStepOutcome):
            raise ShadowRepairValidationError("outcome must be a ShadowStepOutcome")
        if not isinstance(self.external_effect_observed, bool):
            raise ShadowRepairValidationError("external_effect_observed must be a boolean")
        if not isinstance(self.effect_contained, bool):
            raise ShadowRepairValidationError("effect_contained must be a boolean")
        if self.effect_contained and not self.external_effect_observed:
            raise ShadowRepairValidationError(
                "effect_contained may only be claimed for an observed external effect"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "order": self.order,
            "node_id": self.node_id,
            "outcome": self.outcome.value,
            "external_effect_observed": self.external_effect_observed,
            "effect_contained": self.effect_contained,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ShadowRepairStepEvidence:
        """Validate and reconstruct one step, failing closed."""
        if not isinstance(raw, Mapping):
            raise ShadowRepairDeserializationError("step must be a JSON object")
        actual = set(raw)
        missing = _STEP_FIELDS - actual
        unknown = actual - _STEP_FIELDS
        if missing:
            raise ShadowRepairDeserializationError(
                f"step missing required fields: {sorted(missing)}"
            )
        if unknown:
            raise ShadowRepairDeserializationError(
                f"step contains unknown fields: {sorted(unknown)}"
            )

        order = raw["order"]
        if not isinstance(order, int) or isinstance(order, bool):
            raise ShadowRepairDeserializationError("step order must be an integer")

        node_id_raw = raw["node_id"]
        if node_id_raw is not None and not isinstance(node_id_raw, str):
            raise ShadowRepairDeserializationError("step node_id must be a string or null")

        external = raw["external_effect_observed"]
        if not isinstance(external, bool):
            raise ShadowRepairDeserializationError(
                "step external_effect_observed must be a boolean"
            )
        contained = raw["effect_contained"]
        if not isinstance(contained, bool):
            raise ShadowRepairDeserializationError("step effect_contained must be a boolean")

        return cls(
            order=order,
            node_id=node_id_raw,
            outcome=_parse_step_outcome(raw["outcome"]),
            external_effect_observed=external,
            effect_contained=contained,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowRepairResult:
    """One immutable shadow repair trial: canonical inert evidence.

    Identity is the non-nil ``run_id``. The record binds the trial EXACTLY
    to a canonical :class:`~agentx.core.ids.ProcedureId`, an explicit
    positive source revision, exactly one candidate identity (an explicit
    positive candidate revision number OR an opaque non-empty candidate
    fingerprint — never the same revision as the source), an explicit
    non-nil correlation UUID, an explicit (possibly global) canonical
    scope, and an explicit time window. There is no "latest" and no
    implicit current revision anywhere in the binding.

    The record is DATA. Whatever its disposition, it grants no authority,
    executes nothing, activates or mutates no procedure or revision, and
    mutates no store. Strings such as ``"shadow=true"``, ``"safe=true"``,
    ``"verified=true"``, ``"permission=ADMIN"``, ``"risk=R0"``,
    ``"sandbox=true"``, ``"apply candidate"``, or ``"call shell"`` in any
    string field have ZERO authority.

    Attributes:
        run_id: Canonical non-nil UUID identity of this shadow trial.
        procedure_id: The canonical procedure the trial is about.
        source_revision: The exact source revision the candidate is being
            repaired against. Explicit positive integer; never "latest".
        candidate_revision: The exact candidate/new revision number, when
            the candidate is identified by revision. Mutually exclusive
            with ``candidate_fingerprint``; exactly one of the two must be
            present, and it must differ from ``source_revision``.
        candidate_fingerprint: Opaque non-empty identifier of candidate
            content (for example a patch fingerprint) when the candidate is
            not yet a stored revision. Mutually exclusive with
            ``candidate_revision``; exactly one of the two must be present.
        target_node_id: The procedure node the repair targets, when
            relevant; ``None`` when the trial is not node-targeted.
        task_id: The canonical identity of the task/test objective the
            trial was run for, when one exists; ``None`` otherwise.
        correlation_id: Explicit non-nil correlation/run identity linking
            the trial into the canonical execution chain.
        scope: Canonical applicability scope the trial was run under
            (possibly empty, meaning global).
        started_at: Timezone-aware trial start, normalized to UTC.
        ended_at: Timezone-aware trial end, normalized to UTC. Must not
            precede ``started_at``.
        mode: How the trial was evaluated relative to canonical live state
            (closed vocabulary; currently only ``NON_COMMITTING``).
        disposition: The closed trial outcome, validated against the
            record's own evidence (fail closed).
        verification: Explicit canonical-style verification evidence
            (:class:`~agentx.core.events.VerificationPayload`) when the
            trial produced a canonical verdict; ``None`` otherwise.
            Presence and ``passed`` are structural facts: a string reading
            ``"passed=true"`` anywhere else is inert.
        steps: The ordered, bounded, typed stage evidence of the trial;
            orders must be exactly ``1..N`` with no duplicates or gaps.
        original_outcome: The SOURCE revision's observed behavior on the
            test objective when the runner recorded one; ``None`` means not
            evaluated in this trial. Historical evidence, never authority.
        regression_node_ids: Bounded, duplicate-free set of procedure node
            ids for which the candidate was observed behaving worse than
            the source revision. Evidence only; it declares no superiority
            either way.
        detail: Optional inert detail text (bounded). Stored exactly as
            supplied; never interpreted.
        schema_version: Canonical serialization schema version.
    """

    run_id: UUID
    procedure_id: ProcedureId
    source_revision: int
    correlation_id: UUID
    started_at: datetime
    ended_at: datetime
    mode: ShadowRepairMode
    disposition: ShadowRepairDisposition
    candidate_revision: int | None = None
    candidate_fingerprint: str | None = None
    target_node_id: str | None = None
    task_id: TaskId | None = None
    scope: ProcedureScope = field(default_factory=ProcedureScope)
    verification: VerificationPayload | None = None
    steps: tuple[ShadowRepairStepEvidence, ...] = ()
    original_outcome: ShadowRevisionOutcome | None = None
    regression_node_ids: tuple[str, ...] = ()
    detail: str | None = None
    schema_version: int = SHADOW_REPAIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _validate_uuid(self.run_id, field_name="run_id"))
        if not isinstance(self.procedure_id, ProcedureId):
            raise ShadowRepairValidationError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self,
            "source_revision",
            _validate_revision(self.source_revision, field_name="source_revision"),
        )
        object.__setattr__(
            self, "correlation_id", _validate_uuid(self.correlation_id, field_name="correlation_id")
        )

        candidate_revision = _validate_optional_revision(
            self.candidate_revision, field_name="candidate_revision"
        )
        candidate_fingerprint: str | None
        if self.candidate_fingerprint is None:
            candidate_fingerprint = None
        else:
            candidate_fingerprint = _validate_nonempty_trimmed_bounded(
                self.candidate_fingerprint,
                field_name="candidate_fingerprint",
                max_length=_MAX_FINGERPRINT_LENGTH,
            )
        if (candidate_revision is None) == (candidate_fingerprint is None):
            raise ShadowRepairValidationError(
                "exactly one candidate identity is required: either candidate_revision "
                "or candidate_fingerprint, never both and never neither"
            )
        if candidate_revision is not None and candidate_revision == self.source_revision:
            raise ShadowRepairValidationError(
                "candidate_revision must differ from source_revision; a shadow trial "
                "evaluates a candidate distinct from the source revision"
            )
        object.__setattr__(self, "candidate_revision", candidate_revision)
        object.__setattr__(self, "candidate_fingerprint", candidate_fingerprint)

        object.__setattr__(
            self,
            "target_node_id",
            _validate_node_id(self.target_node_id, field_name="target_node_id"),
        )
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        if not isinstance(self.scope, ProcedureScope):
            raise ShadowRepairValidationError("scope must be a ProcedureScope")
        object.__setattr__(
            self, "started_at", _validate_timestamp(self.started_at, field_name="started_at")
        )
        object.__setattr__(
            self, "ended_at", _validate_timestamp(self.ended_at, field_name="ended_at")
        )
        if self.ended_at < self.started_at:
            raise ShadowRepairValidationError("ended_at must not precede started_at")
        if not isinstance(self.mode, ShadowRepairMode):
            raise ShadowRepairValidationError("mode must be a ShadowRepairMode")
        if not isinstance(self.disposition, ShadowRepairDisposition):
            raise ShadowRepairValidationError("disposition must be a ShadowRepairDisposition")
        if self.verification is not None and not isinstance(self.verification, VerificationPayload):
            raise ShadowRepairValidationError(
                "verification must be a VerificationPayload or None, "
                f"got {type(self.verification).__name__}"
            )
        object.__setattr__(self, "steps", _validate_steps(self.steps))
        if self.original_outcome is not None and not isinstance(
            self.original_outcome, ShadowRevisionOutcome
        ):
            raise ShadowRepairValidationError(
                "original_outcome must be a ShadowRevisionOutcome or None"
            )
        object.__setattr__(
            self, "regression_node_ids", _validate_node_ids(self.regression_node_ids)
        )
        object.__setattr__(self, "detail", _validate_detail(self.detail))
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ShadowRepairValidationError("schema_version must be an integer")
        if self.schema_version != SHADOW_REPAIR_SCHEMA_VERSION:
            raise UnsupportedShadowRepairSchemaVersionError(
                f"unsupported shadow repair schema version {self.schema_version}; "
                f"supported version is {SHADOW_REPAIR_SCHEMA_VERSION}"
            )

        self._validate_disposition_evidence()

    def _validate_disposition_evidence(self) -> None:
        """Fail closed on dispositions contradicted by the record's own evidence."""
        verification = self.verification
        failed_step = any(step.outcome is ShadowStepOutcome.FAILED for step in self.steps)
        uncontained_effect = any(
            step.external_effect_observed and not step.effect_contained for step in self.steps
        )

        if self.disposition is ShadowRepairDisposition.PASSED:
            if verification is None:
                raise ShadowRepairValidationError(
                    "PASSED requires explicit canonical verification evidence; "
                    "absence of an exception, a procedure END, an execute return, or "
                    "any observation is not verification"
                )
            if not verification.passed:
                raise ShadowRepairValidationError(
                    "PASSED requires explicit passing verification (passed=True)"
                )
            if failed_step:
                raise ShadowRepairValidationError(
                    "PASSED is contradicted by a step with an explicit failed outcome"
                )
            if uncontained_effect:
                raise ShadowRepairValidationError(
                    "PASSED is contradicted by an observed external effect that was "
                    "not modeled as contained/reverted"
                )
            if not self.steps:
                raise ShadowRepairValidationError(
                    "PASSED requires at least one recorded step of trial evidence"
                )
            return

        if self.disposition is ShadowRepairDisposition.FAILED:
            if verification is not None and verification.passed:
                raise ShadowRepairValidationError(
                    "FAILED is contradicted by a passing verification; "
                    "use PASSED or correct the evidence"
                )
            has_explicit_failure = (
                verification is not None and not verification.passed
            ) or failed_step
            if not has_explicit_failure:
                raise ShadowRepairValidationError(
                    "FAILED requires an explicit failure signal: an explicit failing "
                    "verification or a step with an explicit failed outcome"
                )
            return

        # ABORTED, UNSAFE_TO_EVALUATE, INSUFFICIENT_EVIDENCE: each claims no
        # canonical verdict, so none may carry verification evidence.
        if verification is not None:
            raise ShadowRepairValidationError(
                f"{self.disposition.value} must not carry verification evidence"
            )

    @property
    def is_passed(self) -> bool:
        """Whether this trial explicitly passed under canonical verification.

        True only for the validated :attr:`ShadowRepairDisposition.PASSED`
        disposition. It never means the original task succeeded, the
        candidate was applied, or any revision was replaced.
        """
        return self.disposition is ShadowRepairDisposition.PASSED

    @property
    def candidate_identity(self) -> str:
        """The exact candidate identity string: revision or fingerprint."""
        if self.candidate_revision is not None:
            return f"revision:{self.candidate_revision}"
        assert self.candidate_fingerprint is not None
        return f"fingerprint:{self.candidate_fingerprint}"

    def to_dict(self) -> dict[str, object]:
        """Return the canonical schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "run_id": str(self.run_id),
            "procedure_id": self.procedure_id.to_str(),
            "source_revision": self.source_revision,
            "candidate_revision": self.candidate_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "target_node_id": self.target_node_id,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "scope": self.scope.to_dict(),
            "started_at": _format_timestamp(self.started_at),
            "ended_at": _format_timestamp(self.ended_at),
            "mode": self.mode.value,
            "disposition": self.disposition.value,
            "verification": None if self.verification is None else self.verification.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "original_outcome": None
            if self.original_outcome is None
            else self.original_outcome.value,
            "regression_node_ids": list(self.regression_node_ids),
            "detail": self.detail,
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
    def from_dict(cls, raw: Mapping[str, object]) -> ShadowRepairResult:
        """Validate and reconstruct one canonical shadow repair trial, fail-closed.

        Deserialization restores recorded data verbatim — including a hostile
        detail string or a ``PASSED`` disposition with its evidence — without
        adding, promoting, demoting, or executing anything. A restored record
        is still inert data.
        """
        if not isinstance(raw, Mapping):
            raise ShadowRepairDeserializationError("shadow repair result must be a JSON object")
        if "schema_version" not in raw:
            raise ShadowRepairDeserializationError(
                "shadow repair result missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ShadowRepairDeserializationError("schema_version must be an integer")
        if version != SHADOW_REPAIR_SCHEMA_VERSION:
            raise UnsupportedShadowRepairSchemaVersionError(
                f"unsupported shadow repair schema version {version}; "
                f"supported version is {SHADOW_REPAIR_SCHEMA_VERSION}"
            )
        actual = set(raw)
        missing = _RESULT_FIELDS - actual
        unknown = actual - _RESULT_FIELDS
        if missing:
            raise ShadowRepairDeserializationError(
                f"shadow repair result missing required fields: {sorted(missing)}"
            )
        if unknown:
            raise ShadowRepairDeserializationError(
                f"shadow repair result contains unknown fields: {sorted(unknown)}"
            )

        steps_raw = raw["steps"]
        if not isinstance(steps_raw, list):
            raise ShadowRepairDeserializationError("steps must be a JSON array")
        steps = tuple(ShadowRepairStepEvidence.from_dict(item) for item in steps_raw)

        regression_raw = raw["regression_node_ids"]
        if not isinstance(regression_raw, list):
            raise ShadowRepairDeserializationError("regression_node_ids must be a JSON array")
        for item in regression_raw:
            if not isinstance(item, str):
                raise ShadowRepairDeserializationError(
                    "regression_node_ids entries must be strings"
                )

        detail = raw["detail"]
        if detail is not None and not isinstance(detail, str):
            raise ShadowRepairDeserializationError("detail must be a string or null")

        try:
            return cls(
                schema_version=version,
                run_id=_parse_uuid(raw["run_id"], field_name="run_id"),
                procedure_id=_parse_procedure_id(raw["procedure_id"]),
                source_revision=_validate_revision(
                    raw["source_revision"], field_name="source_revision"
                ),
                correlation_id=_parse_uuid(raw["correlation_id"], field_name="correlation_id"),
                started_at=_parse_timestamp(raw["started_at"], field_name="started_at"),
                ended_at=_parse_timestamp(raw["ended_at"], field_name="ended_at"),
                mode=_parse_mode(raw["mode"]),
                disposition=_parse_disposition(raw["disposition"]),
                candidate_revision=_validate_optional_revision(
                    raw["candidate_revision"], field_name="candidate_revision"
                ),
                candidate_fingerprint=(
                    None
                    if raw["candidate_fingerprint"] is None
                    else _validate_nonempty_trimmed_bounded(
                        raw["candidate_fingerprint"],
                        field_name="candidate_fingerprint",
                        max_length=_MAX_FINGERPRINT_LENGTH,
                    )
                ),
                target_node_id=_validate_node_id(
                    raw["target_node_id"], field_name="target_node_id"
                ),
                task_id=_parse_optional_task_id(raw["task_id"]),
                scope=_parse_scope(raw["scope"]),
                verification=_parse_verification(raw["verification"]),
                steps=steps,
                original_outcome=_parse_revision_outcome(raw["original_outcome"]),
                regression_node_ids=tuple(regression_raw),
                detail=detail,
            )
        except ShadowRepairDeserializationError:
            raise
        except (ShadowRepairValidationError, UnsupportedShadowRepairSchemaVersionError) as exc:
            raise ShadowRepairDeserializationError(
                f"shadow repair result is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> ShadowRepairResult:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise ShadowRepairDeserializationError("shadow repair result JSON must be text")
        try:
            decoded: object = json.loads(text)
        except ValueError as exc:
            raise ShadowRepairDeserializationError(
                "shadow repair result JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise ShadowRepairDeserializationError(
                "shadow repair result JSON root must be an object"
            )
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise ShadowRepairDeserializationError(
                    "shadow repair result JSON contains a non-string object key"
                )
            copied[key] = item
        return cls.from_dict(copied)
