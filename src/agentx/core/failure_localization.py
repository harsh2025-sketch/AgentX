"""Canonical inert failure-localization contract (C4.02).

AgentX must be able to say *where in the attempted execution chain* the
available structured evidence explicitly points. This module owns that
vocabulary and the smallest immutable record that carries it. It owns
nothing else.

What this module is:

    * :class:`FailureLocationKind` — the canonical, closed localization-target
      vocabulary (task, capability, procedure, procedure node, action,
      observation, verification, environment, dependency, unknown).
    * :class:`LocalizationEvidence` — the explicit structured evidence
      contract. Callers name a target kind and supply the matching canonical
      identity references. Nothing is inferred from free text.
    * :class:`FailureLocalization` — one immutable, deterministic,
      JSON-serializable record of a localization result.
    * :func:`localize_failure` — pure packaging of validated evidence into a
      localization record. No store query, no model, no keyword scan.

What this module is emphatically NOT:

    * It is not diagnosis. Nothing here decides that a procedure node is
      "logically wrong", generates a patch, or ranks causes. C4.03 owns
      procedure-node diagnosis.
    * It is not classification. :class:`~agentx.core.failure_taxonomy.FailureCategory`
      remains owned by C4.01. Category and location are orthogonal: a
      ``PROCEDURE`` category never fabricates a procedure-node location, and a
      localization never rewrites a classification.
    * It is not authority. A localization grants nothing, revokes nothing, and
      proves nothing. Authority belongs exclusively to ``agentx.kernel``.
    * It is not a repair, retry, fallback, escalation, or suppression
      instruction. It never mutates Task state, procedures, knowledge,
      budgets, risk, permissions, EmergencyStop, or verification.
    * It carries no probability, confidence, score, ranking, embedding, or
      model invocation.
    * It never inspects arbitrary text, tracebacks, or summaries for keywords
      such as ``"permission"``, ``"button"``, ``"API"``, or ``"verify"`` to
      invent a location.

Evidence-driven fail-closed policy: :attr:`FailureLocationKind.UNKNOWN` (also
exposed as the unlocalized result via :attr:`FailureLocalization.is_unlocalized`)
is a first-class, always-valid outcome when structured evidence cannot justify
a more specific target. Deserialization never coerces an unrecognized kind
string into ``UNKNOWN`` — unknown input is rejected.

Identity reuse: this module introduces no competing identifier types. It reuses
``TaskId``, ``CapabilityId``, ``ProcedureId``, ``EpisodeId``, and
``NegativeExperienceId`` from :mod:`agentx.core.ids`, the execution-chain
``correlation_id`` UUID already used by C2.06/C2.10/C4.01, and graph-local
procedure-node identity as a validated string (the canonical
``ProcedureNodeId`` type lives outward in ``agentx.procedures`` and must not be
imported into ``agentx.core``). Optional linkage to a C4.01
:class:`~agentx.core.failure_taxonomy.FailureClassification` is by value
reference only and never drives the location kind.

Deliberate non-goals owned by later tasks: procedure-node diagnosis (C4.03),
environment-change detection (C4.04), patch generation (C4.05), repair
validation (C4.06), shadow repair (C4.07), version/rollback (C4.08), and repair
budgets/anti-loop (C4.09).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.failure_taxonomy import (
    FailureClassification,
    FailureClassificationDeserializationError,
    FailureClassificationValidationError,
)
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    NegativeExperienceId,
    ProcedureId,
    TaskId,
)

__all__ = [
    "CANONICAL_FAILURE_LOCATION_KINDS",
    "FAILURE_LOCALIZATION_SCHEMA_VERSION",
    "FailureLocalization",
    "FailureLocalizationDeserializationError",
    "FailureLocalizationValidationError",
    "FailureLocationKind",
    "LocalizationEvidence",
    "UnsupportedFailureLocalizationSchemaVersionError",
    "localize_failure",
]

FAILURE_LOCALIZATION_SCHEMA_VERSION: Final[int] = 1

_MAX_SUMMARY_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 4_096
_MAX_NODE_ID_LENGTH: Final[int] = 256
_MAX_ACTION_NAME_LENGTH: Final[int] = 256
_MAX_REF_LENGTH: Final[int] = 256

_LOCALIZATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "kind",
        "summary",
        "localized_at",
        "detail",
        "task_id",
        "capability_id",
        "procedure_id",
        "procedure_node_id",
        "action_name",
        "environment_ref",
        "dependency_ref",
        "episode_id",
        "negative_experience_id",
        "correlation_id",
        "classification",
    }
)


class FailureLocalizationValidationError(ValueError):
    """Raised when failure-localization data violates the canonical contract."""


class FailureLocalizationDeserializationError(FailureLocalizationValidationError):
    """Raised when encoded failure-localization data cannot be decoded safely."""


class UnsupportedFailureLocalizationSchemaVersionError(FailureLocalizationDeserializationError):
    """Raised when encoded data uses an unsupported failure-localization schema."""


class FailureLocationKind(StrEnum):
    """The canonical, closed AgentX failure-localization target vocabulary.

    Each member names *where structured evidence explicitly points* in the
    attempted execution chain. No member implies a remedy, a diagnosis that a
    node is logically wrong, a retry decision, a permission change, or a
    prohibition, and no member is ordered above or below another.

    Members:
        TASK: Evidence explicitly names the canonical task under which the
            failure was observed.
        CAPABILITY: Evidence explicitly names the capability identity of the
            attempt.
        PROCEDURE: Evidence explicitly names the stored procedure identity, but
            not a specific node within it.
        PROCEDURE_NODE: Evidence explicitly names both a procedure identity and
            a graph-local procedure-node identity. This records the pointer
            only; C4.03 owns whether the node is logically wrong.
        ACTION: Evidence explicitly points at the action/attempt stage of the
            execution chain (correlation identity, optionally the action name).
        OBSERVATION: Evidence explicitly points at the observation stage of the
            execution chain.
        VERIFICATION: Evidence explicitly points at the verification stage of
            the execution chain.
        ENVIRONMENT: Evidence explicitly names an environment reference the
            attempt depended on.
        DEPENDENCY: Evidence explicitly names an external dependency reference
            the attempt relied on.
        UNKNOWN: Structured evidence does not justify any more specific target.
            This is the fail-closed / unlocalized value and is always valid.
    """

    TASK = "task"
    CAPABILITY = "capability"
    PROCEDURE = "procedure"
    PROCEDURE_NODE = "procedure_node"
    ACTION = "action"
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    ENVIRONMENT = "environment"
    DEPENDENCY = "dependency"
    UNKNOWN = "unknown"


#: The canonical location kinds in their declared order. Exported so callers may
#: enumerate the vocabulary without re-declaring it.
CANONICAL_FAILURE_LOCATION_KINDS: Final[tuple[FailureLocationKind, ...]] = tuple(
    FailureLocationKind
)


def _has_control_characters(value: str, *, allow_whitespace: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_whitespace else frozenset()
    return any(
        character < " " or character == "\x7f" for character in value if character not in allowed
    )


def _validate_summary(value: object) -> str:
    if not isinstance(value, str):
        raise FailureLocalizationValidationError("summary must be a string")
    if not value or value != value.strip():
        raise FailureLocalizationValidationError("summary must be non-empty and trimmed")
    if len(value) > _MAX_SUMMARY_LENGTH:
        raise FailureLocalizationValidationError(
            f"summary must be at most {_MAX_SUMMARY_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise FailureLocalizationValidationError("summary must not contain control characters")
    return value


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationValidationError("detail must be a string or None")
    if not value or value != value.strip():
        raise FailureLocalizationValidationError(
            "detail must be non-empty and trimmed when provided"
        )
    if len(value) > _MAX_DETAIL_LENGTH:
        raise FailureLocalizationValidationError(
            f"detail must be at most {_MAX_DETAIL_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=True):
        raise FailureLocalizationValidationError("detail must not contain control characters")
    return value


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise FailureLocalizationValidationError("localized_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FailureLocalizationValidationError("localized_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("localized_at must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            "localized_at is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FailureLocalizationDeserializationError("localized_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise FailureLocalizationValidationError("correlation_id must be a UUID or None")
    if value.int == 0:
        raise FailureLocalizationValidationError("correlation_id must not be the nil UUID")
    return value


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None or isinstance(value, TaskId):
        return value
    raise FailureLocalizationValidationError("task_id must be a TaskId or None")


def _validate_optional_capability_id(value: object) -> CapabilityId | None:
    if value is None or isinstance(value, CapabilityId):
        return value
    raise FailureLocalizationValidationError("capability_id must be a CapabilityId or None")


def _validate_optional_procedure_id(value: object) -> ProcedureId | None:
    if value is None or isinstance(value, ProcedureId):
        return value
    raise FailureLocalizationValidationError("procedure_id must be a ProcedureId or None")


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None or isinstance(value, EpisodeId):
        return value
    raise FailureLocalizationValidationError("episode_id must be an EpisodeId or None")


def _validate_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None or isinstance(value, NegativeExperienceId):
        return value
    raise FailureLocalizationValidationError(
        "negative_experience_id must be a NegativeExperienceId or None"
    )


def _validate_optional_ref(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationValidationError(f"{field_name} must be a string or None")
    if not value or value != value.strip():
        raise FailureLocalizationValidationError(
            f"{field_name} must be non-empty and trimmed when provided"
        )
    if len(value) > max_length:
        raise FailureLocalizationValidationError(
            f"{field_name} must be at most {max_length} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise FailureLocalizationValidationError(
            f"{field_name} must not contain control characters"
        )
    return value


def _validate_optional_classification(value: object) -> FailureClassification | None:
    if value is None or isinstance(value, FailureClassification):
        return value
    raise FailureLocalizationValidationError(
        "classification must be a FailureClassification or None"
    )


def _parse_kind(value: object) -> FailureLocationKind:
    """Decode exactly one canonical location kind and fail closed otherwise.

    Unrecognized input is rejected. It is deliberately NOT downgraded to
    ``UNKNOWN``: silently accepting foreign vocabulary would fabricate a
    localization the data never contained.
    """
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("kind must be a string")
    try:
        return FailureLocationKind(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            f"kind must be one of {[member.value for member in CANONICAL_FAILURE_LOCATION_KINDS]}"
        ) from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError("task_id is not a valid TaskId") from exc


def _parse_optional_capability_id(value: object) -> CapabilityId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("capability_id must be a string or null")
    try:
        return CapabilityId.parse(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            "capability_id is not a valid CapabilityId"
        ) from exc


def _parse_optional_procedure_id(value: object) -> ProcedureId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("procedure_id must be a string or null")
    try:
        return ProcedureId.parse(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            "procedure_id is not a valid ProcedureId"
        ) from exc


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("episode_id must be a string or null")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            "episode_id is not a valid EpisodeId"
        ) from exc


def _parse_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError(
            "negative_experience_id must be a string or null"
        )
    try:
        return NegativeExperienceId.parse(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError(
            "negative_experience_id is not a valid NegativeExperienceId"
        ) from exc


def _parse_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureLocalizationDeserializationError("correlation_id must be a string or null")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FailureLocalizationDeserializationError("correlation_id is not a valid UUID") from exc
    if parsed.int == 0:
        raise FailureLocalizationDeserializationError("correlation_id must not be the nil UUID")
    return parsed


def _parse_optional_classification(value: object) -> FailureClassification | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise FailureLocalizationDeserializationError(
            "classification must be a JSON object or null"
        )
    try:
        return FailureClassification.from_dict(value)
    except FailureClassificationDeserializationError as exc:
        raise FailureLocalizationDeserializationError(f"classification is invalid: {exc}") from exc


def _require_exact_fields(raw: Mapping[str, object]) -> None:
    actual = set(raw)
    missing = _LOCALIZATION_FIELDS - actual
    unknown = actual - _LOCALIZATION_FIELDS
    if missing:
        raise FailureLocalizationDeserializationError(
            f"failure localization missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise FailureLocalizationDeserializationError(
            f"failure localization contains unknown fields: {sorted(unknown)}"
        )


def _present_target_fields(
    *,
    capability_id: CapabilityId | None,
    procedure_id: ProcedureId | None,
    procedure_node_id: str | None,
    action_name: str | None,
    environment_ref: str | None,
    dependency_ref: str | None,
) -> frozenset[str]:
    present: set[str] = set()
    if capability_id is not None:
        present.add("capability_id")
    if procedure_id is not None:
        present.add("procedure_id")
    if procedure_node_id is not None:
        present.add("procedure_node_id")
    if action_name is not None:
        present.add("action_name")
    if environment_ref is not None:
        present.add("environment_ref")
    if dependency_ref is not None:
        present.add("dependency_ref")
    return frozenset(present)


def _validate_kind_evidence(
    kind: FailureLocationKind,
    *,
    task_id: TaskId | None,
    capability_id: CapabilityId | None,
    procedure_id: ProcedureId | None,
    procedure_node_id: str | None,
    action_name: str | None,
    environment_ref: str | None,
    dependency_ref: str | None,
    correlation_id: UUID | None,
) -> None:
    """Enforce that target-specific identity matches the declared kind.

    Ambient chain context (``task_id`` when kind is not TASK, ``episode_id``,
    ``correlation_id`` when not required by the stage kinds, classification
    linkage) is allowed without fabricating a different target. Target-specific
    fields that belong to another kind are rejected so category-shaped or
    hostile text cannot smuggle a location claim.
    """
    present = _present_target_fields(
        capability_id=capability_id,
        procedure_id=procedure_id,
        procedure_node_id=procedure_node_id,
        action_name=action_name,
        environment_ref=environment_ref,
        dependency_ref=dependency_ref,
    )

    if kind is FailureLocationKind.UNKNOWN:
        if present:
            raise FailureLocalizationValidationError(
                "UNKNOWN/unlocalized localization must not carry target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.TASK:
        if task_id is None:
            raise FailureLocalizationValidationError(
                "TASK localization requires an explicit task_id"
            )
        if present:
            raise FailureLocalizationValidationError(
                "TASK localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.CAPABILITY:
        if capability_id is None:
            raise FailureLocalizationValidationError(
                "CAPABILITY localization requires an explicit capability_id"
            )
        if present - {"capability_id"}:
            raise FailureLocalizationValidationError(
                "CAPABILITY localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.PROCEDURE:
        if procedure_id is None:
            raise FailureLocalizationValidationError(
                "PROCEDURE localization requires an explicit procedure_id"
            )
        if present - {"procedure_id"}:
            raise FailureLocalizationValidationError(
                "PROCEDURE localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.PROCEDURE_NODE:
        if procedure_id is None:
            raise FailureLocalizationValidationError(
                "PROCEDURE_NODE localization requires an explicit procedure_id"
            )
        if procedure_node_id is None:
            raise FailureLocalizationValidationError(
                "PROCEDURE_NODE localization requires an explicit procedure_node_id"
            )
        if present - {"procedure_id", "procedure_node_id"}:
            raise FailureLocalizationValidationError(
                "PROCEDURE_NODE localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.ACTION:
        if correlation_id is None:
            raise FailureLocalizationValidationError(
                "ACTION localization requires an explicit correlation_id for the attempt"
            )
        if present - {"action_name"}:
            raise FailureLocalizationValidationError(
                "ACTION localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.OBSERVATION:
        if correlation_id is None:
            raise FailureLocalizationValidationError(
                "OBSERVATION localization requires an explicit correlation_id for the attempt"
            )
        if present:
            raise FailureLocalizationValidationError(
                "OBSERVATION localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.VERIFICATION:
        if correlation_id is None:
            raise FailureLocalizationValidationError(
                "VERIFICATION localization requires an explicit correlation_id for the attempt"
            )
        if present:
            raise FailureLocalizationValidationError(
                "VERIFICATION localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.ENVIRONMENT:
        if environment_ref is None:
            raise FailureLocalizationValidationError(
                "ENVIRONMENT localization requires an explicit environment_ref"
            )
        if present - {"environment_ref"}:
            raise FailureLocalizationValidationError(
                "ENVIRONMENT localization must not carry foreign target-specific identity fields"
            )
        return

    if kind is FailureLocationKind.DEPENDENCY:
        if dependency_ref is None:
            raise FailureLocalizationValidationError(
                "DEPENDENCY localization requires an explicit dependency_ref"
            )
        if present - {"dependency_ref"}:
            raise FailureLocalizationValidationError(
                "DEPENDENCY localization must not carry foreign target-specific identity fields"
            )
        return

    raise FailureLocalizationValidationError(f"unsupported failure location kind: {kind!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalizationEvidence:
    """Explicit structured evidence used to localize a failure.

    The caller supplies a typed :class:`FailureLocationKind` and the matching
    canonical identity references already known to the system. This contract
    never inspects ``summary``, ``detail``, classification text, stack traces,
    or any other free-form content to invent a target.

    Ambient chain context (``task_id`` when not localizing to TASK,
    ``episode_id``, ``correlation_id`` when not required, optional C4.01
    ``classification``) may be attached without changing the declared target.
    Target-specific fields that do not belong to the declared kind are rejected.
    """

    kind: FailureLocationKind
    task_id: TaskId | None = None
    capability_id: CapabilityId | None = None
    procedure_id: ProcedureId | None = None
    procedure_node_id: str | None = None
    action_name: str | None = None
    environment_ref: str | None = None
    dependency_ref: str | None = None
    episode_id: EpisodeId | None = None
    negative_experience_id: NegativeExperienceId | None = None
    correlation_id: UUID | None = None
    classification: FailureClassification | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FailureLocationKind):
            raise FailureLocalizationValidationError(
                "kind must be a FailureLocationKind member; "
                "this contract never infers a location from text"
            )
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(
            self, "capability_id", _validate_optional_capability_id(self.capability_id)
        )
        object.__setattr__(self, "procedure_id", _validate_optional_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "procedure_node_id",
            _validate_optional_ref(
                self.procedure_node_id,
                field_name="procedure_node_id",
                max_length=_MAX_NODE_ID_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "action_name",
            _validate_optional_ref(
                self.action_name, field_name="action_name", max_length=_MAX_ACTION_NAME_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "environment_ref",
            _validate_optional_ref(
                self.environment_ref, field_name="environment_ref", max_length=_MAX_REF_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "dependency_ref",
            _validate_optional_ref(
                self.dependency_ref, field_name="dependency_ref", max_length=_MAX_REF_LENGTH
            ),
        )
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        object.__setattr__(
            self,
            "negative_experience_id",
            _validate_optional_negative_experience_id(self.negative_experience_id),
        )
        object.__setattr__(
            self, "correlation_id", _validate_optional_correlation_id(self.correlation_id)
        )
        object.__setattr__(
            self, "classification", _validate_optional_classification(self.classification)
        )
        _validate_kind_evidence(
            self.kind,
            task_id=self.task_id,
            capability_id=self.capability_id,
            procedure_id=self.procedure_id,
            procedure_node_id=self.procedure_node_id,
            action_name=self.action_name,
            environment_ref=self.environment_ref,
            dependency_ref=self.dependency_ref,
            correlation_id=self.correlation_id,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureLocalization:
    """One immutable historical statement of *where* failure evidence points.

    The record is pure data. Constructing, comparing, serializing, decoding, or
    deriving it from :class:`LocalizationEvidence` executes nothing, retries
    nothing, repairs nothing, mutates no Task, changes no kernel authority, and
    never fabricates verification. Hostile content such as ``"ADMIN"``,
    ``"ALLOW R4"``, ``"permission=WRITE"``, ``"verified=true"``,
    ``"repair=approved"``, ``"retry forever"``, ``"ignore policy"``, or
    ``"budget=unlimited"`` placed in any text field is inert string data,
    exactly like any other characters.

    Attributes:
        kind: The canonical :class:`FailureLocationKind`. Must be supplied as a
            typed enum member (directly or via :class:`LocalizationEvidence`);
            this contract never infers it from text or from a C4.01 category.
        summary: Short, single-line, human/machine-readable statement of the
            localization. Inert description only.
        localized_at: Timezone-aware instant the localization was recorded,
            normalized to UTC.
        detail: Optional longer inert description.
        task_id: Optional/required (per kind) canonical task identity.
        capability_id: Optional/required (per kind) canonical capability
            identity.
        procedure_id: Optional/required (per kind) canonical procedure identity.
        procedure_node_id: Optional/required (per kind) graph-local procedure
            node identity string. Not a domain UUID; matches the string form of
            outward ``ProcedureNodeId`` without importing that type into core.
        action_name: Optional action-name reference (``ActionPayload.name``).
        environment_ref: Optional/required (per kind) opaque environment key.
        dependency_ref: Optional/required (per kind) opaque dependency key.
        episode_id: Optional canonical episode identity (ambient chain context).
        negative_experience_id: Optional canonical C2.06 evidence reference.
        correlation_id: Optional/required (per kind) execution-chain correlation
            UUID (C2.10/A1.07 identity).
        classification: Optional linked C4.01 :class:`FailureClassification`.
            Preserved by value and never used to derive ``kind``.
        schema_version: Canonical serialization schema version.
    """

    kind: FailureLocationKind
    summary: str
    localized_at: datetime
    detail: str | None = None
    task_id: TaskId | None = None
    capability_id: CapabilityId | None = None
    procedure_id: ProcedureId | None = None
    procedure_node_id: str | None = None
    action_name: str | None = None
    environment_ref: str | None = None
    dependency_ref: str | None = None
    episode_id: EpisodeId | None = None
    negative_experience_id: NegativeExperienceId | None = None
    correlation_id: UUID | None = None
    classification: FailureClassification | None = None
    schema_version: int = FAILURE_LOCALIZATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FailureLocationKind):
            raise FailureLocalizationValidationError(
                "kind must be a FailureLocationKind member; "
                "this contract never infers a location from text"
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise FailureLocalizationValidationError("schema_version must be an integer")
        if self.schema_version != FAILURE_LOCALIZATION_SCHEMA_VERSION:
            raise FailureLocalizationValidationError(
                f"schema_version must be {FAILURE_LOCALIZATION_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "summary", _validate_summary(self.summary))
        object.__setattr__(self, "localized_at", _validate_timestamp(self.localized_at))
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(
            self, "capability_id", _validate_optional_capability_id(self.capability_id)
        )
        object.__setattr__(self, "procedure_id", _validate_optional_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "procedure_node_id",
            _validate_optional_ref(
                self.procedure_node_id,
                field_name="procedure_node_id",
                max_length=_MAX_NODE_ID_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "action_name",
            _validate_optional_ref(
                self.action_name, field_name="action_name", max_length=_MAX_ACTION_NAME_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "environment_ref",
            _validate_optional_ref(
                self.environment_ref, field_name="environment_ref", max_length=_MAX_REF_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "dependency_ref",
            _validate_optional_ref(
                self.dependency_ref, field_name="dependency_ref", max_length=_MAX_REF_LENGTH
            ),
        )
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        object.__setattr__(
            self,
            "negative_experience_id",
            _validate_optional_negative_experience_id(self.negative_experience_id),
        )
        object.__setattr__(
            self, "correlation_id", _validate_optional_correlation_id(self.correlation_id)
        )
        object.__setattr__(
            self, "classification", _validate_optional_classification(self.classification)
        )
        _validate_kind_evidence(
            self.kind,
            task_id=self.task_id,
            capability_id=self.capability_id,
            procedure_id=self.procedure_id,
            procedure_node_id=self.procedure_node_id,
            action_name=self.action_name,
            environment_ref=self.environment_ref,
            dependency_ref=self.dependency_ref,
            correlation_id=self.correlation_id,
        )

    @property
    def is_unlocalized(self) -> bool:
        """Whether this record fails closed as :attr:`FailureLocationKind.UNKNOWN`."""
        return self.kind is FailureLocationKind.UNKNOWN

    @property
    def is_unknown(self) -> bool:
        """Alias of :attr:`is_unlocalized` for fail-closed UNKNOWN semantics."""
        return self.is_unlocalized

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "summary": self.summary,
            "localized_at": _format_timestamp(self.localized_at),
            "detail": self.detail,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "capability_id": None if self.capability_id is None else self.capability_id.to_str(),
            "procedure_id": None if self.procedure_id is None else self.procedure_id.to_str(),
            "procedure_node_id": self.procedure_node_id,
            "action_name": self.action_name,
            "environment_ref": self.environment_ref,
            "dependency_ref": self.dependency_ref,
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "negative_experience_id": (
                None
                if self.negative_experience_id is None
                else self.negative_experience_id.to_str()
            ),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "classification": (
                None if self.classification is None else self.classification.to_dict()
            ),
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
    def from_dict(cls, raw: Mapping[str, object]) -> FailureLocalization:
        """Validate and reconstruct one canonical localization, failing closed."""
        if not isinstance(raw, Mapping):
            raise FailureLocalizationDeserializationError(
                "failure localization must be a JSON object"
            )
        if "schema_version" not in raw:
            raise FailureLocalizationDeserializationError(
                "failure localization missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise FailureLocalizationDeserializationError("schema_version must be an integer")
        if version != FAILURE_LOCALIZATION_SCHEMA_VERSION:
            raise UnsupportedFailureLocalizationSchemaVersionError(
                f"unsupported failure localization schema version {version}; "
                f"supported version is {FAILURE_LOCALIZATION_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw)

        detail = raw["detail"]
        if detail is not None and not isinstance(detail, str):
            raise FailureLocalizationDeserializationError("detail must be a string or null")
        summary = raw["summary"]
        if not isinstance(summary, str):
            raise FailureLocalizationDeserializationError("summary must be a string")
        procedure_node_id = raw["procedure_node_id"]
        if procedure_node_id is not None and not isinstance(procedure_node_id, str):
            raise FailureLocalizationDeserializationError(
                "procedure_node_id must be a string or null"
            )
        action_name = raw["action_name"]
        if action_name is not None and not isinstance(action_name, str):
            raise FailureLocalizationDeserializationError("action_name must be a string or null")
        environment_ref = raw["environment_ref"]
        if environment_ref is not None and not isinstance(environment_ref, str):
            raise FailureLocalizationDeserializationError(
                "environment_ref must be a string or null"
            )
        dependency_ref = raw["dependency_ref"]
        if dependency_ref is not None and not isinstance(dependency_ref, str):
            raise FailureLocalizationDeserializationError("dependency_ref must be a string or null")

        try:
            return cls(
                kind=_parse_kind(raw["kind"]),
                summary=summary,
                localized_at=_parse_timestamp(raw["localized_at"]),
                detail=detail,
                task_id=_parse_optional_task_id(raw["task_id"]),
                capability_id=_parse_optional_capability_id(raw["capability_id"]),
                procedure_id=_parse_optional_procedure_id(raw["procedure_id"]),
                procedure_node_id=procedure_node_id,
                action_name=action_name,
                environment_ref=environment_ref,
                dependency_ref=dependency_ref,
                episode_id=_parse_optional_episode_id(raw["episode_id"]),
                negative_experience_id=_parse_optional_negative_experience_id(
                    raw["negative_experience_id"]
                ),
                correlation_id=_parse_optional_correlation_id(raw["correlation_id"]),
                classification=_parse_optional_classification(raw["classification"]),
                schema_version=version,
            )
        except FailureLocalizationDeserializationError:
            raise
        except FailureLocalizationValidationError as exc:
            raise FailureLocalizationDeserializationError(
                f"failure localization is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> FailureLocalization:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise FailureLocalizationDeserializationError("failure localization JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise FailureLocalizationDeserializationError(
                "failure localization JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise FailureLocalizationDeserializationError(
                "failure localization JSON root must be an object"
            )
        return cls.from_dict(decoded)


def localize_failure(
    evidence: LocalizationEvidence,
    *,
    summary: str,
    localized_at: datetime,
    detail: str | None = None,
) -> FailureLocalization:
    """Package explicit structured evidence into one immutable localization.

    This is a pure data transform. It copies validated identity references from
    *evidence*, attaches the caller-supplied inert summary/detail, and returns a
    :class:`FailureLocalization`. It does not:

    - inspect summary/detail/classification text for keywords;
    - derive a location from a C4.01 :class:`FailureCategory`;
    - query stores, invoke models, or analyze trajectories;
    - diagnose, repair, retry, execute, or mutate any runtime state.

    When *evidence* declares :attr:`FailureLocationKind.UNKNOWN`, the result is
    the fail-closed unlocalized localization.
    """
    if not isinstance(evidence, LocalizationEvidence):
        raise FailureLocalizationValidationError(
            "evidence must be a LocalizationEvidence instance; "
            "free-form text is not accepted as localization evidence"
        )
    try:
        return FailureLocalization(
            kind=evidence.kind,
            summary=summary,
            localized_at=localized_at,
            detail=detail,
            task_id=evidence.task_id,
            capability_id=evidence.capability_id,
            procedure_id=evidence.procedure_id,
            procedure_node_id=evidence.procedure_node_id,
            action_name=evidence.action_name,
            environment_ref=evidence.environment_ref,
            dependency_ref=evidence.dependency_ref,
            episode_id=evidence.episode_id,
            negative_experience_id=evidence.negative_experience_id,
            correlation_id=evidence.correlation_id,
            classification=evidence.classification,
        )
    except FailureClassificationValidationError as exc:  # pragma: no cover - defensive
        raise FailureLocalizationValidationError(
            f"linked classification is invalid: {exc}"
        ) from exc
