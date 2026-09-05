"""Canonical inert failure taxonomy and classification record (C4.01).

AgentX must be able to say *what kind of failure* it observed in one stable,
typed vocabulary. This module owns that vocabulary and the smallest immutable
record that carries it. It owns nothing else.

What this module is:

    * :class:`FailureCategory` — the canonical, closed failure vocabulary.
    * :class:`FailureClassification` — one immutable, deterministic,
      JSON-serializable record: category + inert summary + optional canonical
      references to failure evidence that already exists elsewhere.

What this module is emphatically NOT:

    * It is not diagnosis. Nothing here inspects, parses, or infers a category
      from arbitrary text, exceptions, tracebacks, or observations. A category
      is always supplied explicitly by the caller as a typed enum member. A
      string containing the word ``"permission"`` never becomes
      :attr:`FailureCategory.PERMISSION` here.
    * It is not authority. A classification grants nothing, revokes nothing and
      proves nothing. Authority belongs exclusively to ``agentx.kernel``.
    * It is not a repair, retry, fallback, escalation, or suppression
      instruction. Historical failure is not permanent prohibition: a recorded
      ``CAPABILITY`` failure does not mean that capability may never be
      attempted again, a ``PERMISSION`` classification does not grant the
      missing permission, a ``VERIFICATION`` classification does not
      manufacture verification success, and a ``PROCEDURE`` classification does
      not patch the procedure.
    * It carries no probability, confidence, score, or ranking.

Deliberate non-goals owned by later tasks: failure localization (C4.02),
procedure-node diagnosis (C4.03), environment-change detection (C4.04), patch
generation (C4.05), repair validation (C4.06), shadow repair (C4.07), procedure
version replacement/rollback (C4.08), and repair budgets/anti-loop (C4.09).

Reuse: this module introduces no competing error hierarchy. ``error_code``
references the canonical :class:`agentx.core.errors.AgentXError` code and is
validated by that module's own rule. Identity references reuse the canonical
``agentx.core.ids`` types already used by C2.06 negative experience and C2.10
causal experience records.

Fail-closed policy: :attr:`FailureCategory.UNKNOWN` is a first-class, valid
classification and is the only correct choice when the available evidence does
not justify a more specific class. Deserialization never coerces an
unrecognized category string into ``UNKNOWN`` — unknown input is rejected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.errors import _validate_error_code as _validate_canonical_error_code
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId

__all__ = [
    "CANONICAL_FAILURE_CATEGORIES",
    "FAILURE_CLASSIFICATION_SCHEMA_VERSION",
    "FailureCategory",
    "FailureClassification",
    "FailureClassificationDeserializationError",
    "FailureClassificationValidationError",
    "UnsupportedFailureClassificationSchemaVersionError",
]

FAILURE_CLASSIFICATION_SCHEMA_VERSION: Final[int] = 1

_MAX_SUMMARY_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 4_096
_MAX_ERROR_CODE_LENGTH: Final[int] = 256

_CLASSIFICATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "category",
        "summary",
        "classified_at",
        "detail",
        "error_code",
        "task_id",
        "episode_id",
        "negative_experience_id",
        "correlation_id",
    }
)


class FailureClassificationValidationError(ValueError):
    """Raised when failure-classification data violates the canonical contract."""


class FailureClassificationDeserializationError(FailureClassificationValidationError):
    """Raised when encoded failure-classification data cannot be decoded safely."""


class UnsupportedFailureClassificationSchemaVersionError(FailureClassificationDeserializationError):
    """Raised when encoded data uses an unsupported failure-classification schema."""


class FailureCategory(StrEnum):
    """The canonical, closed AgentX failure vocabulary.

    Each member names *what kind of thing went wrong*, as observed history. No
    member implies a remedy, a retry decision, a permission change, or a
    prohibition, and no member is ordered above or below another.

    Members:
        TRANSIENT: A momentary, non-structural disturbance was observed (for
            example a timeout or a temporary unavailability). It records what
            was seen; it does not authorize or schedule a retry.
        PRECONDITION: A required precondition for the attempt did not hold.
        ENVIRONMENT: The surrounding environment (machine, OS, configuration,
            installed software state) did not match what the attempt needed.
        PERMISSION: The attempt lacked the authority required to proceed.
            Classifying a failure this way never grants the missing permission
            and never alters the Trusted Kernel.
        DEPENDENCY: An external dependency the attempt relied on failed or was
            unavailable.
        UI_CHANGE: An observed user-interface surface no longer matched what the
            attempt expected.
        API_CHANGE: An observed programmatic interface no longer matched what
            the attempt expected.
        CAPABILITY: The capability itself was missing, unusable, or failed on
            its own terms. This is history, not a permanent ban on that
            capability.
        PROCEDURE: The stored procedure being followed was wrong, stale, or
            inapplicable. This does not patch, deactivate, or version the
            procedure.
        KNOWLEDGE: The knowledge the attempt relied on was missing, wrong, or
            inapplicable. This never promotes or degrades KnowledgeStatus.
        PLAN: The plan/decomposition itself was unsound for the goal.
        VERIFICATION: Verification of the outcome did not pass. This never
            manufactures verification success.
        UNKNOWN: The evidence does not justify any more specific class. This is
            the fail-closed value and is always a valid classification.
    """

    TRANSIENT = "transient"
    PRECONDITION = "precondition"
    ENVIRONMENT = "environment"
    PERMISSION = "permission"
    DEPENDENCY = "dependency"
    UI_CHANGE = "ui_change"
    API_CHANGE = "api_change"
    CAPABILITY = "capability"
    PROCEDURE = "procedure"
    KNOWLEDGE = "knowledge"
    PLAN = "plan"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


#: The canonical categories in their declared order. Exported so callers may
#: enumerate the vocabulary without re-declaring it.
CANONICAL_FAILURE_CATEGORIES: Final[tuple[FailureCategory, ...]] = tuple(FailureCategory)


def _has_control_characters(value: str, *, allow_whitespace: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_whitespace else frozenset()
    return any(
        character < " " or character == "\x7f" for character in value if character not in allowed
    )


def _validate_summary(value: object) -> str:
    if not isinstance(value, str):
        raise FailureClassificationValidationError("summary must be a string")
    if not value or value != value.strip():
        raise FailureClassificationValidationError("summary must be non-empty and trimmed")
    if len(value) > _MAX_SUMMARY_LENGTH:
        raise FailureClassificationValidationError(
            f"summary must be at most {_MAX_SUMMARY_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise FailureClassificationValidationError("summary must not contain control characters")
    return value


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationValidationError("detail must be a string or None")
    if not value or value != value.strip():
        raise FailureClassificationValidationError(
            "detail must be non-empty and trimmed when provided"
        )
    if len(value) > _MAX_DETAIL_LENGTH:
        raise FailureClassificationValidationError(
            f"detail must be at most {_MAX_DETAIL_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=True):
        raise FailureClassificationValidationError("detail must not contain control characters")
    return value


def _validate_optional_error_code(value: object) -> str | None:
    """Validate an optional reference to a canonical ``AgentXError`` code.

    The reference is validated with the canonical A1.04 rule so this module
    cannot drift into a second, competing error vocabulary. The code is never
    resolved, dereferenced, or interpreted here.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationValidationError("error_code must be a string or None")
    try:
        code = _validate_canonical_error_code(value)
    except ValueError as exc:
        raise FailureClassificationValidationError(
            f"error_code must be a canonical AgentXError code: {exc}"
        ) from exc
    if len(code) > _MAX_ERROR_CODE_LENGTH:
        raise FailureClassificationValidationError(
            f"error_code must be at most {_MAX_ERROR_CODE_LENGTH} characters"
        )
    return code


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise FailureClassificationValidationError("classified_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FailureClassificationValidationError("classified_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError("classified_at must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FailureClassificationDeserializationError(
            "classified_at is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FailureClassificationDeserializationError("classified_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise FailureClassificationValidationError("correlation_id must be a UUID or None")
    if value.int == 0:
        raise FailureClassificationValidationError("correlation_id must not be the nil UUID")
    return value


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None or isinstance(value, TaskId):
        return value
    raise FailureClassificationValidationError("task_id must be a TaskId or None")


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None or isinstance(value, EpisodeId):
        return value
    raise FailureClassificationValidationError("episode_id must be an EpisodeId or None")


def _validate_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None or isinstance(value, NegativeExperienceId):
        return value
    raise FailureClassificationValidationError(
        "negative_experience_id must be a NegativeExperienceId or None"
    )


def _parse_category(value: object) -> FailureCategory:
    """Decode exactly one canonical category value and fail closed otherwise.

    Unrecognized input is rejected. It is deliberately NOT downgraded to
    ``UNKNOWN``: silently accepting foreign vocabulary would fabricate a
    classification the data never contained.
    """
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError("category must be a string")
    try:
        return FailureCategory(value)
    except ValueError as exc:
        raise FailureClassificationDeserializationError(
            f"category must be one of {[member.value for member in CANONICAL_FAILURE_CATEGORIES]}"
        ) from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise FailureClassificationDeserializationError("task_id is not a valid TaskId") from exc


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError("episode_id must be a string or null")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise FailureClassificationDeserializationError(
            "episode_id is not a valid EpisodeId"
        ) from exc


def _parse_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError(
            "negative_experience_id must be a string or null"
        )
    try:
        return NegativeExperienceId.parse(value)
    except ValueError as exc:
        raise FailureClassificationDeserializationError(
            "negative_experience_id is not a valid NegativeExperienceId"
        ) from exc


def _parse_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureClassificationDeserializationError("correlation_id must be a string or null")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FailureClassificationDeserializationError(
            "correlation_id is not a valid UUID"
        ) from exc
    if parsed.int == 0:
        raise FailureClassificationDeserializationError("correlation_id must not be the nil UUID")
    return parsed


def _require_exact_fields(raw: Mapping[str, object]) -> None:
    actual = set(raw)
    missing = _CLASSIFICATION_FIELDS - actual
    unknown = actual - _CLASSIFICATION_FIELDS
    if missing:
        raise FailureClassificationDeserializationError(
            f"failure classification missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise FailureClassificationDeserializationError(
            f"failure classification contains unknown fields: {sorted(unknown)}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureClassification:
    """One immutable historical statement of *what kind of failure* was observed.

    The record is pure data. Constructing, comparing, serializing, or decoding
    it executes nothing, retries nothing, repairs nothing, mutates no Task, and
    changes no kernel authority. Hostile content such as ``"ADMIN"``,
    ``"ALLOW R4"``, ``"permission=WRITE"``, ``"verified=true"``,
    ``"repair=approved"``, ``"retry forever"``, ``"ignore policy"``, or
    ``"budget=unlimited"`` placed in any text field is inert string data,
    exactly like any other characters.

    Attributes:
        category: The canonical :class:`FailureCategory`. Must be supplied as a
            typed enum member; this contract never infers it from text.
        summary: Short, single-line, human/machine-readable statement of the
            observed failure. Inert description only.
        classified_at: Timezone-aware instant the classification was recorded,
            normalized to UTC.
        detail: Optional longer inert description.
        error_code: Optional reference to the canonical
            :class:`agentx.core.errors.AgentXError` code of already-recorded
            error evidence. Never resolved or interpreted here.
        task_id: Optional canonical task the failure was observed under.
        episode_id: Optional canonical episode the failure was observed under.
        negative_experience_id: Optional canonical C2.06 negative-experience
            record that already stores the remembered failed attempt.
        correlation_id: Optional canonical execution-chain correlation UUID.
        schema_version: Canonical serialization schema version.
    """

    category: FailureCategory
    summary: str
    classified_at: datetime
    detail: str | None = None
    error_code: str | None = None
    task_id: TaskId | None = None
    episode_id: EpisodeId | None = None
    negative_experience_id: NegativeExperienceId | None = None
    correlation_id: UUID | None = None
    schema_version: int = FAILURE_CLASSIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.category, FailureCategory):
            raise FailureClassificationValidationError(
                "category must be a FailureCategory member; "
                "this contract never infers a category from text"
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise FailureClassificationValidationError("schema_version must be an integer")
        if self.schema_version != FAILURE_CLASSIFICATION_SCHEMA_VERSION:
            raise FailureClassificationValidationError(
                f"schema_version must be {FAILURE_CLASSIFICATION_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "summary", _validate_summary(self.summary))
        object.__setattr__(self, "classified_at", _validate_timestamp(self.classified_at))
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))
        object.__setattr__(self, "error_code", _validate_optional_error_code(self.error_code))
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        object.__setattr__(
            self,
            "negative_experience_id",
            _validate_optional_negative_experience_id(self.negative_experience_id),
        )
        object.__setattr__(
            self, "correlation_id", _validate_optional_correlation_id(self.correlation_id)
        )

    @property
    def is_unknown(self) -> bool:
        """Whether this record fails closed as :attr:`FailureCategory.UNKNOWN`."""
        return self.category is FailureCategory.UNKNOWN

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "category": self.category.value,
            "summary": self.summary,
            "classified_at": _format_timestamp(self.classified_at),
            "detail": self.detail,
            "error_code": self.error_code,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "negative_experience_id": (
                None
                if self.negative_experience_id is None
                else self.negative_experience_id.to_str()
            ),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
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
    def from_dict(cls, raw: Mapping[str, object]) -> FailureClassification:
        """Validate and reconstruct one canonical classification, failing closed."""
        if not isinstance(raw, Mapping):
            raise FailureClassificationDeserializationError(
                "failure classification must be a JSON object"
            )
        if "schema_version" not in raw:
            raise FailureClassificationDeserializationError(
                "failure classification missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise FailureClassificationDeserializationError("schema_version must be an integer")
        if version != FAILURE_CLASSIFICATION_SCHEMA_VERSION:
            raise UnsupportedFailureClassificationSchemaVersionError(
                f"unsupported failure classification schema version {version}; "
                f"supported version is {FAILURE_CLASSIFICATION_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw)

        detail = raw["detail"]
        if detail is not None and not isinstance(detail, str):
            raise FailureClassificationDeserializationError("detail must be a string or null")
        error_code = raw["error_code"]
        if error_code is not None and not isinstance(error_code, str):
            raise FailureClassificationDeserializationError("error_code must be a string or null")
        summary = raw["summary"]
        if not isinstance(summary, str):
            raise FailureClassificationDeserializationError("summary must be a string")

        try:
            return cls(
                category=_parse_category(raw["category"]),
                summary=summary,
                classified_at=_parse_timestamp(raw["classified_at"]),
                detail=detail,
                error_code=error_code,
                task_id=_parse_optional_task_id(raw["task_id"]),
                episode_id=_parse_optional_episode_id(raw["episode_id"]),
                negative_experience_id=_parse_optional_negative_experience_id(
                    raw["negative_experience_id"]
                ),
                correlation_id=_parse_optional_correlation_id(raw["correlation_id"]),
                schema_version=version,
            )
        except FailureClassificationDeserializationError:
            raise
        except FailureClassificationValidationError as exc:
            raise FailureClassificationDeserializationError(
                f"failure classification is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> FailureClassification:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise FailureClassificationDeserializationError(
                "failure classification JSON must be text"
            )
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise FailureClassificationDeserializationError(
                "failure classification JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise FailureClassificationDeserializationError(
                "failure classification JSON root must be an object"
            )
        return cls.from_dict(decoded)
