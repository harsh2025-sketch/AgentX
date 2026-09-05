"""Canonical inert artifact-reference record for AgentX durable storage (C2.04).

An :class:`ArtifactRecord` is DATA ONLY: a durable, stable reference to an
artifact that AgentX generated or observed. Artifact contents themselves stay
external to this contract — this module performs no I/O of any kind and owns
no blob platform, object storage, filesystem manager, downloader, parser,
compressor, deduplicator, or media pipeline.

A stored reference is inert text. Whatever a reference looks like — a
relative path, a ``file:``/``http(s)`` URL, source code, a privilege string —
storing, serializing, deserializing, or inspecting a record must not open,
fetch, import, execute, trust, or dereference it. A record claiming
``verified=true`` or ``ADMIN`` is exactly as authoritative as any other
string: not at all. Authority semantics belong exclusively to
``agentx.kernel``.

Field minimalism is deliberate: only canonical identity (:class:`ArtifactId`
from ``agentx.core.ids``), a storage-level kind, the opaque reference, creation
time, and the justified optional metadata (media type, integrity digest,
size, originating task/episode correlation) exist. Optional integrity and
size metadata are descriptive claims captured at record time; nothing here
verifies them against artifact content because this contract never touches
content.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems except the canonical identifiers in ``agentx.core.ids``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import ArtifactId, DomainId, EpisodeId, TaskId

__all__ = [
    "CURRENT_ARTIFACT_SCHEMA_VERSION",
    "ArtifactDigest",
    "ArtifactDigestAlgorithm",
    "ArtifactKind",
    "ArtifactRecord",
    "ArtifactValidationError",
    "UnsupportedArtifactSchemaVersionError",
]

CURRENT_ARTIFACT_SCHEMA_VERSION: Final[int] = 1

_MAX_REFERENCE_LENGTH: Final[int] = 4_096
_MAX_MEDIA_TYPE_LENGTH: Final[int] = 255
_LOWER_HEX_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]+$")


class ArtifactValidationError(ValueError):
    """Raised when an artifact record violates the canonical contract."""


class UnsupportedArtifactSchemaVersionError(ArtifactValidationError):
    """Raised when encoded record data uses a schema version this code cannot read."""


class ArtifactKind(StrEnum):
    """Storage-level kind vocabulary for persisted artifact references.

    The vocabulary distinguishes where a reference came from and nothing
    else. It carries no trust, no lifecycle, and no behavior: future tasks
    add members rather than this contract growing a general type system.
    """

    GENERATED = "generated"
    OBSERVED = "observed"


class ArtifactDigestAlgorithm(StrEnum):
    """Controlled digest algorithm vocabulary for optional integrity metadata.

    The algorithm name is a label for how the producer computed the digest.
    This contract never hashes, verifies, or otherwise touches artifact
    content.
    """

    SHA256 = "sha256"


#: Hexadecimal digest length per algorithm (characters of lowercase hex).
_DIGEST_HEX_LENGTH: Final[dict[ArtifactDigestAlgorithm, int]] = {
    ArtifactDigestAlgorithm.SHA256: 64,
}


def _validate_nonempty_trimmed(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ArtifactValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise ArtifactValidationError(f"{field_name} must be at most {max_length} characters")
    return value


def _validate_optional_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    return _validate_nonempty_trimmed(value, field_name=field_name, max_length=max_length)


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ArtifactValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArtifactValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ArtifactValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtifactValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_size(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactValidationError(f"{field_name} must be an integer")
    if value < 0:
        raise ArtifactValidationError(f"{field_name} must not be negative")
    return value


def _parse_id[IdT: DomainId](value: object, *, id_type: type[IdT], field_name: str) -> IdT:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a UUID string")
    try:
        return id_type.parse(value)
    except ValueError as exc:
        raise ArtifactValidationError(
            f"{field_name} must be a valid non-nil UUID string: {value!r}"
        ) from exc


def _require_id_instance(value: object, *, id_type: type[DomainId], field_name: str) -> None:
    if not isinstance(value, id_type):
        raise ArtifactValidationError(f"{field_name} must be a {id_type.__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactDigest:
    """Optional integrity metadata: algorithm label plus lowercase hex digest.

    A digest recorded here is a claim captured alongside the reference. The
    contract never computes, compares, or verifies it against artifact
    content, because artifact content is out of scope for artifact references.
    """

    algorithm: ArtifactDigestAlgorithm
    hex_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.algorithm, ArtifactDigestAlgorithm):
            raise ArtifactValidationError("digest algorithm must be an ArtifactDigestAlgorithm")
        if not isinstance(self.hex_digest, str):
            raise ArtifactValidationError("digest must be a lowercase hexadecimal string")
        expected_length = _DIGEST_HEX_LENGTH[self.algorithm]
        if len(self.hex_digest) != expected_length or not _LOWER_HEX_RE.match(self.hex_digest):
            raise ArtifactValidationError(
                f"{self.algorithm.value} digest must be exactly {expected_length} "
                "lowercase hexadecimal characters"
            )

    def to_dict(self) -> dict[str, str]:
        return {"algorithm": self.algorithm.value, "hex_digest": self.hex_digest}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ArtifactDigest:
        actual = set(raw)
        expected = {"algorithm", "hex_digest"}
        if actual != expected:
            raise ArtifactValidationError(
                f"digest must contain exactly {sorted(expected)}; got {sorted(actual)}"
            )
        algorithm_raw = raw["algorithm"]
        if not isinstance(algorithm_raw, str):
            raise ArtifactValidationError("digest algorithm must be a string")
        try:
            algorithm = ArtifactDigestAlgorithm(algorithm_raw)
        except ValueError as exc:
            raise ArtifactValidationError(f"unknown digest algorithm: {algorithm_raw!r}") from exc
        hex_digest = raw["hex_digest"]
        if not isinstance(hex_digest, str):
            raise ArtifactValidationError("digest must be a string")
        return cls(algorithm=algorithm, hex_digest=hex_digest)


_ARTIFACT_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "artifact_id",
        "kind",
        "reference",
        "media_type",
        "digest",
        "size_bytes",
        "task_id",
        "episode_id",
        "created_at",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactRecord:
    """Immutable canonical reference to one AgentX-generated or -observed artifact.

    Fields (all descriptive):

        artifact_id — canonical :class:`agentx.core.ids.ArtifactId` identity.
        kind — storage-level :class:`ArtifactKind` vocabulary only.
        reference — stable opaque locator text; NEVER opened, fetched,
            imported, executed, or trusted by this contract or its store.
        created_at — when the reference record entered life (UTC-normalized).
        media_type — optional opaque content/media type label, not parsed.
        digest — optional integrity claim; never verified here.
        size_bytes — optional size claim; no I/O implied.
        task_id / episode_id — optional canonical correlation identities.
            Correlation is a label, not a foreign key: nothing is resolved,
            joined, or validated against other stores.

    Records are frozen snapshots. There is deliberately no update, replace,
    or promotion surface on this contract.
    """

    artifact_id: ArtifactId
    kind: ArtifactKind
    reference: str
    created_at: datetime
    media_type: str | None = None
    digest: ArtifactDigest | None = None
    size_bytes: int | None = None
    task_id: TaskId | None = None
    episode_id: EpisodeId | None = None
    schema_version: int = CURRENT_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_id_instance(self.artifact_id, id_type=ArtifactId, field_name="artifact_id")
        if not isinstance(self.kind, ArtifactKind):
            raise ArtifactValidationError("kind must be an ArtifactKind")
        object.__setattr__(
            self,
            "reference",
            _validate_nonempty_trimmed(
                self.reference,
                field_name="reference",
                max_length=_MAX_REFERENCE_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "created_at",
            _validate_timestamp(self.created_at, field_name="created_at"),
        )
        object.__setattr__(
            self,
            "media_type",
            _validate_optional_text(
                self.media_type,
                field_name="media_type",
                max_length=_MAX_MEDIA_TYPE_LENGTH,
            ),
        )
        if self.digest is not None and not isinstance(self.digest, ArtifactDigest):
            raise ArtifactValidationError("digest must be an ArtifactDigest or None")
        if self.size_bytes is not None:
            object.__setattr__(
                self,
                "size_bytes",
                _validate_size(self.size_bytes, field_name="size_bytes"),
            )
        if self.task_id is not None:
            _require_id_instance(self.task_id, id_type=TaskId, field_name="task_id")
        if self.episode_id is not None:
            _require_id_instance(self.episode_id, id_type=EpisodeId, field_name="episode_id")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ArtifactValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_ARTIFACT_SCHEMA_VERSION:
            raise UnsupportedArtifactSchemaVersionError(
                f"unsupported artifact schema version {self.schema_version}; "
                f"supported version is {CURRENT_ARTIFACT_SCHEMA_VERSION}"
            )

    @classmethod
    def create(
        cls,
        *,
        kind: ArtifactKind,
        reference: str,
        media_type: str | None = None,
        digest: ArtifactDigest | None = None,
        size_bytes: int | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
        artifact_id: ArtifactId | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactRecord:
        """Create one inert reference with fresh identity/time unless supplied."""
        return cls(
            artifact_id=ArtifactId.create() if artifact_id is None else artifact_id,
            kind=kind,
            reference=reference,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            media_type=media_type,
            digest=digest,
            size_bytes=size_bytes,
            task_id=task_id,
            episode_id=episode_id,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id.to_str(),
            "kind": self.kind.value,
            "reference": self.reference,
            "media_type": self.media_type,
            "digest": None if self.digest is None else self.digest.to_dict(),
            "size_bytes": self.size_bytes,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "created_at": _format_timestamp(self.created_at),
        }

    def to_json(self) -> str:
        """Serialize to deterministic JSON text without executable object hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ArtifactRecord:
        """Validate and deserialize a canonical JSON-compatible record object.

        Deserialization restores persisted data verbatim — including hostile
        reference, media type, or metadata text — without dereferencing or
        interpreting anything. A restored record is still inert data.
        """
        if "schema_version" not in raw:
            raise ArtifactValidationError("record missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ArtifactValidationError("schema_version must be an integer")
        if version != CURRENT_ARTIFACT_SCHEMA_VERSION:
            raise UnsupportedArtifactSchemaVersionError(
                f"unsupported artifact schema version {version}; "
                f"supported version is {CURRENT_ARTIFACT_SCHEMA_VERSION}"
            )

        actual = set(raw)
        if actual != _ARTIFACT_RECORD_FIELDS:
            missing = _ARTIFACT_RECORD_FIELDS - actual
            unknown = actual - _ARTIFACT_RECORD_FIELDS
            if missing:
                raise ArtifactValidationError(f"record missing required fields: {sorted(missing)}")
            raise ArtifactValidationError(f"record contains unknown fields: {sorted(unknown)}")

        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ArtifactValidationError("kind must be a string")
        try:
            kind = ArtifactKind(kind_raw)
        except ValueError as exc:
            raise ArtifactValidationError(f"unknown artifact kind: {kind_raw!r}") from exc

        digest_raw = raw["digest"]
        digest: ArtifactDigest | None
        if digest_raw is None:
            digest = None
        elif isinstance(digest_raw, Mapping):
            digest_object: dict[str, object] = {}
            for key, item in digest_raw.items():
                if not isinstance(key, str):
                    raise ArtifactValidationError("digest contains a non-string object key")
                digest_object[key] = item
            digest = ArtifactDigest.from_dict(digest_object)
        else:
            raise ArtifactValidationError("digest must be a JSON object or null")

        size_raw = raw["size_bytes"]
        size_bytes = None if size_raw is None else _validate_size(size_raw, field_name="size_bytes")
        task_raw = raw["task_id"]
        episode_raw = raw["episode_id"]
        media_raw = raw["media_type"]

        return cls(
            artifact_id=_parse_id(raw["artifact_id"], id_type=ArtifactId, field_name="artifact_id"),
            kind=kind,
            reference=_validate_nonempty_trimmed(
                raw["reference"],
                field_name="reference",
                max_length=_MAX_REFERENCE_LENGTH,
            ),
            created_at=_parse_timestamp(raw["created_at"], field_name="created_at"),
            media_type=_validate_optional_text(
                media_raw,
                field_name="media_type",
                max_length=_MAX_MEDIA_TYPE_LENGTH,
            ),
            digest=digest,
            size_bytes=size_bytes,
            task_id=(
                None
                if task_raw is None
                else _parse_id(task_raw, id_type=TaskId, field_name="task_id")
            ),
            episode_id=(
                None
                if episode_raw is None
                else _parse_id(episode_raw, id_type=EpisodeId, field_name="episode_id")
            ),
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> ArtifactRecord:
        """Deserialize JSON text without dynamic imports or arbitrary construction."""
        if not isinstance(raw, str):
            raise ArtifactValidationError("record JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError("record JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ArtifactValidationError("record JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise ArtifactValidationError("record JSON contains a non-string object key")
            copied[key] = item
        return cls.from_dict(copied)
