"""Research-provider data boundary for A4.03.

A4.03 defines inert, research-specific contracts only.  It does not perform
research, choose a provider, invoke a model, access the network or browser,
execute a capability, persist state, mutate Hive, or create authority.

The causal boundary is explicit::

    ResearchObjective -> ResearchRequest -> future authorized provider execution
    -> ResearchResponse

A ResearchObjective alone therefore cannot produce provider evidence.  External
evidence carried by a ResearchResponse remains untrusted provenance data; a
response is not verified Knowledge and provenance is not truth.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.cognition.research_objective import ResearchObjective
from agentx.core.knowledge import ProvenanceReference

__all__ = [
    "CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION",
    "ResearchProviderAvailability",
    "ResearchProviderFailure",
    "ResearchProviderIdentity",
    "ResearchProviderValidationError",
    "ResearchRequest",
    "ResearchResponse",
    "UnsupportedResearchProviderSchemaVersionError",
    "research_response_from_dict",
    "research_response_from_json",
    "research_response_to_json",
]

CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION: Final[int] = 1
_MAX_ID_LENGTH: Final[int] = 128
_MAX_KIND_LENGTH: Final[int] = 128
_MAX_NAME_LENGTH: Final[int] = 512

_IDENTITY_FIELDS: Final = frozenset({"research_provider_id", "kind", "name"})
_RESPONSE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "request_id",
        "research_provider_id",
        "availability",
        "evidence",
        "failure",
    }
)


class ResearchProviderValidationError(ValueError):
    """Raised when A4.03 provider-boundary data is malformed or contradictory."""


class UnsupportedResearchProviderSchemaVersionError(ResearchProviderValidationError):
    """Raised when encoded A4.03 data uses an unsupported schema version."""


def _validate_inert_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ResearchProviderValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ResearchProviderValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in ("\x00", "\r")):
        raise ResearchProviderValidationError(
            f"{field_name} must not contain NUL or carriage-return characters"
        )
    if len(value) > max_length:
        raise ResearchProviderValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    return value


def _validate_optional_inert_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    return _validate_inert_text(value, field_name=field_name, max_length=max_length)


class ResearchProviderAvailability(StrEnum):
    """Provider-reported availability facts; never authority."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class ResearchProviderFailure(StrEnum):
    """Provider-reported failure facts; never verification or authority."""

    UNKNOWN = "unknown"
    RATE_LIMITED = "rate-limited"
    BLOCKED = "blocked"
    UNSUPPORTED_OBJECTIVE = "unsupported-objective"
    INTERNAL_ERROR = "internal-error"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchProviderIdentity:
    """Research-specific provider identity with no executable behavior."""

    research_provider_id: str
    kind: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_provider_id",
            _validate_inert_text(
                self.research_provider_id,
                field_name="research_provider_id",
                max_length=_MAX_ID_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "kind",
            _validate_optional_inert_text(
                self.kind,
                field_name="kind",
                max_length=_MAX_KIND_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "name",
            _validate_optional_inert_text(
                self.name,
                field_name="name",
                max_length=_MAX_NAME_LENGTH,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "research_provider_id": self.research_provider_id,
            "kind": self.kind,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ResearchProviderIdentity:
        if not isinstance(raw, Mapping):
            raise ResearchProviderValidationError("research_provider_id must be a JSON object")
        actual = set(raw)
        if actual != _IDENTITY_FIELDS:
            missing = _IDENTITY_FIELDS - actual
            unknown = actual - _IDENTITY_FIELDS
            if missing:
                raise ResearchProviderValidationError(
                    f"research provider identity missing required fields: {sorted(missing)}"
                )
            raise ResearchProviderValidationError(
                f"research provider identity contains unknown fields: {sorted(unknown)}"
            )
        provider_id = raw["research_provider_id"]
        kind = raw["kind"]
        name = raw["name"]
        if not isinstance(provider_id, str):
            raise ResearchProviderValidationError("research_provider_id must be a string")
        if kind is not None and not isinstance(kind, str):
            raise ResearchProviderValidationError("kind must be a string or null")
        if name is not None and not isinstance(name, str):
            raise ResearchProviderValidationError("name must be a string or null")
        return cls(research_provider_id=provider_id, kind=kind, name=name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchRequest:
    """One inert request binding a caller identity to a ResearchObjective."""

    request_id: str
    objective: ResearchObjective

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            _validate_inert_text(
                self.request_id,
                field_name="request_id",
                max_length=_MAX_ID_LENGTH,
            ),
        )
        if not isinstance(self.objective, ResearchObjective):
            raise TypeError("objective must be a ResearchObjective")

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "objective": self.objective.to_dict(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchResponse:
    """Untrusted provider evidence for one explicit ResearchRequest identity.

    ``AVAILABLE`` means the provider reports that it served the request; it is
    not permission, liveness proof, or verification.  An available provider may
    legitimately return zero evidence (for example, an empty result set).

    ``UNAVAILABLE`` and ``UNSUPPORTED`` cannot carry evidence because those
    states structurally say the request was not served.  ``ERROR`` may retain
    partial untrusted evidence, but every non-available state requires an
    explicit failure value; ``UNKNOWN`` exists when no more specific reason is
    known.
    """

    request_id: str
    research_provider_id: ResearchProviderIdentity
    availability: ResearchProviderAvailability
    evidence: tuple[ProvenanceReference, ...]
    failure: ResearchProviderFailure | None = None
    schema_version: int = CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            _validate_inert_text(
                self.request_id,
                field_name="request_id",
                max_length=_MAX_ID_LENGTH,
            ),
        )
        if not isinstance(self.research_provider_id, ResearchProviderIdentity):
            raise TypeError("research_provider_id must be a ResearchProviderIdentity")
        if not isinstance(self.availability, ResearchProviderAvailability):
            raise TypeError("availability must be a ResearchProviderAvailability")
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        for index, reference in enumerate(self.evidence):
            if not isinstance(reference, ProvenanceReference):
                raise TypeError(f"evidence[{index}] must be a ProvenanceReference")
        if self.failure is not None and not isinstance(self.failure, ResearchProviderFailure):
            raise TypeError("failure must be a ResearchProviderFailure or None")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ResearchProviderValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION:
            raise UnsupportedResearchProviderSchemaVersionError(
                f"unsupported research provider schema version {self.schema_version}; "
                f"supported version is {CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION}"
            )

        if self.availability is ResearchProviderAvailability.AVAILABLE:
            if self.failure is not None:
                raise ResearchProviderValidationError(
                    "AVAILABLE research response cannot carry a failure"
                )
            return

        if self.failure is None:
            raise ResearchProviderValidationError(
                f"{self.availability.value.upper()} research response requires a failure"
            )

        if (
            self.availability
            in {
                ResearchProviderAvailability.UNAVAILABLE,
                ResearchProviderAvailability.UNSUPPORTED,
            }
            and self.evidence
        ):
            raise ResearchProviderValidationError(
                f"{self.availability.value.upper()} research response cannot carry evidence"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "research_provider_id": self.research_provider_id.to_dict(),
            "availability": self.availability.value,
            "evidence": [reference.to_dict() for reference in self.evidence],
            "failure": None if self.failure is None else self.failure.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ResearchResponse:
        if not isinstance(raw, Mapping):
            raise ResearchProviderValidationError("response must be a JSON object")
        actual = set(raw)
        if actual != _RESPONSE_FIELDS:
            missing = _RESPONSE_FIELDS - actual
            unknown = actual - _RESPONSE_FIELDS
            if missing:
                raise ResearchProviderValidationError(
                    f"response missing required fields: {sorted(missing)}"
                )
            raise ResearchProviderValidationError(
                f"response contains unknown fields: {sorted(unknown)}"
            )

        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ResearchProviderValidationError("schema_version must be an integer")
        if version != CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION:
            raise UnsupportedResearchProviderSchemaVersionError(
                f"unsupported research provider schema version {version}; "
                f"supported version is {CURRENT_RESEARCH_PROVIDER_SCHEMA_VERSION}"
            )

        request_id = raw["request_id"]
        if not isinstance(request_id, str):
            raise ResearchProviderValidationError("request_id must be a string")

        provider_raw = raw["research_provider_id"]
        if not isinstance(provider_raw, Mapping):
            raise ResearchProviderValidationError("research_provider_id must be a JSON object")
        provider = ResearchProviderIdentity.from_dict(provider_raw)

        availability_raw = raw["availability"]
        if not isinstance(availability_raw, str):
            raise ResearchProviderValidationError("availability must be a string")
        try:
            availability = ResearchProviderAvailability(availability_raw)
        except ValueError as exc:
            raise ResearchProviderValidationError(
                f"unknown availability: {availability_raw!r}"
            ) from exc

        evidence_raw = raw["evidence"]
        if not isinstance(evidence_raw, list):
            raise ResearchProviderValidationError("evidence must be a JSON array")
        evidence: list[ProvenanceReference] = []
        for index, item in enumerate(evidence_raw):
            if not isinstance(item, Mapping):
                raise ResearchProviderValidationError(f"evidence[{index}] must be a JSON object")
            evidence.append(ProvenanceReference.from_dict(item))

        failure_raw = raw["failure"]
        failure: ResearchProviderFailure | None = None
        if failure_raw is not None:
            if not isinstance(failure_raw, str):
                raise ResearchProviderValidationError("failure must be a string or null")
            try:
                failure = ResearchProviderFailure(failure_raw)
            except ValueError as exc:
                raise ResearchProviderValidationError(f"unknown failure: {failure_raw!r}") from exc

        return cls(
            request_id=request_id,
            research_provider_id=provider,
            availability=availability,
            evidence=tuple(evidence),
            failure=failure,
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> ResearchResponse:
        if not isinstance(raw, str):
            raise ResearchProviderValidationError("response JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResearchProviderValidationError("response JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ResearchProviderValidationError("response JSON root must be an object")
        return cls.from_dict(decoded)


def research_response_to_json(response: ResearchResponse) -> str:
    """Serialize one inert ResearchResponse."""
    if not isinstance(response, ResearchResponse):
        raise TypeError("response must be a ResearchResponse")
    return response.to_json()


def research_response_from_dict(raw: Mapping[str, object]) -> ResearchResponse:
    """Validate and rebuild one inert ResearchResponse."""
    return ResearchResponse.from_dict(raw)


def research_response_from_json(raw: str) -> ResearchResponse:
    """Validate and rebuild one inert ResearchResponse from JSON."""
    return ResearchResponse.from_json(raw)
