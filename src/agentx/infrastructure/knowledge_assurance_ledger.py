"""Restart-safe knowledge assurance and revalidation ledger (AX-122/123).

The implementation deliberately reuses the canonical append-only EventJournal
instead of creating a competing persistence system. Assurance observations,
revalidation requests, and completed results are inert observation events.
Reconstruction is deterministic and bounded; replay never publishes events or
executes untrusted content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Final
from uuid import UUID, uuid5

from agentx.core.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    Event,
    EventType,
    ObservationPayload,
)
from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeValidationError
from agentx.core.knowledge_assurance import (
    KnowledgeAssuranceMetadata,
    KnowledgeConfidenceState,
    KnowledgeRevalidation,
    KnowledgeRevalidationRequest,
    assurance_after_revalidation,
)
from agentx.core.provenance import EvidenceReference
from agentx.infrastructure.event_journal import DuplicateEventError, EventJournal
from agentx.infrastructure.knowledge_store import KnowledgeNotFoundError, KnowledgeStore

__all__ = [
    "DuplicateKnowledgeRevalidationError",
    "KnowledgeAssuranceHistoryTooLargeError",
    "KnowledgeAssuranceLedger",
    "KnowledgeAssuranceLedgerError",
    "KnowledgeRevalidationNotRequestedError",
]

_SOURCE: Final = "agentx.knowledge_assurance"
_KIND_KEY: Final = "agentx_kind"
_KIND_SEED: Final = "knowledge.assurance.seed"
_KIND_REQUEST: Final = "knowledge.revalidation.requested"
_KIND_RESULT: Final = "knowledge.revalidation.completed"
_DEFAULT_MAX_REPLAY_EVENTS: Final = 10_000
_REQUEST_NAMESPACE: Final = UUID("32c5f830-808a-4c3f-8476-30e693468b8b")


class KnowledgeAssuranceLedgerError(RuntimeError):
    """Base error for bounded assurance/revalidation history operations."""


class KnowledgeAssuranceHistoryTooLargeError(KnowledgeAssuranceLedgerError):
    """Raised when bounded replay cannot prove a complete assurance history."""


class KnowledgeRevalidationNotRequestedError(KnowledgeAssuranceLedgerError):
    """Raised when a completion has no matching persisted request."""


class DuplicateKnowledgeRevalidationError(KnowledgeAssuranceLedgerError):
    """Raised when one revalidation identifier is completed more than once."""


def _request_event_id(revalidation_id: UUID) -> UUID:
    return uuid5(_REQUEST_NAMESPACE, f"request:{revalidation_id}")


def _canonical_evidence_key(reference: EvidenceReference) -> str:
    return json.dumps(
        reference.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _merge_evidence(
    first: tuple[EvidenceReference, ...],
    second: tuple[EvidenceReference, ...],
) -> tuple[EvidenceReference, ...]:
    merged: dict[str, EvidenceReference] = {}
    for reference in first + second:
        merged[_canonical_evidence_key(reference)] = reference
    return tuple(merged[key] for key in sorted(merged))


def _payload_mapping(event: Event) -> Mapping[str, object] | None:
    if event.event_type is not EventType.OBSERVATION_RECORDED:
        return None
    if event.source != _SOURCE:
        return None
    payload = event.payload
    if not isinstance(payload, ObservationPayload) or not isinstance(
        payload.value, Mapping
    ):
        return None
    return payload.value


def _kind(payload: Mapping[str, object]) -> str | None:
    value = payload.get(_KIND_KEY)
    return value if isinstance(value, str) else None


def _parse_seed(payload: Mapping[str, object]) -> KnowledgeAssuranceMetadata:
    raw = payload.get("metadata")
    if not isinstance(raw, Mapping):
        raise KnowledgeValidationError("assurance seed metadata must be an object")
    copied: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise KnowledgeValidationError(
                "assurance seed has a non-string metadata key"
            )
        copied[key] = value
    return KnowledgeAssuranceMetadata.from_dict(copied)


def _parse_request(payload: Mapping[str, object]) -> KnowledgeRevalidationRequest:
    raw = payload.get("request")
    if not isinstance(raw, Mapping):
        raise KnowledgeValidationError("revalidation request payload must be an object")
    expected = {"revalidation_id", "knowledge_id", "requested_at", "source"}
    if set(raw) != expected:
        raise KnowledgeValidationError("revalidation request fields are malformed")
    rid = raw["revalidation_id"]
    kid = raw["knowledge_id"]
    requested = raw["requested_at"]
    source = raw["source"]
    if not all(isinstance(value, str) for value in (rid, kid, requested, source)):
        raise KnowledgeValidationError(
            "revalidation request scalar fields must be strings"
        )
    try:
        revalidation_id = UUID(rid)
        knowledge_id = KnowledgeId.parse(kid)
        requested_at = datetime.fromisoformat(
            requested.replace("Z", "+00:00")
        ).astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise KnowledgeValidationError(
            "revalidation request identifiers/timestamp are malformed"
        ) from exc
    return KnowledgeRevalidationRequest(
        revalidation_id=revalidation_id,
        knowledge_id=knowledge_id,
        requested_at=requested_at,
        source=source,
    )


def _request_dict(request: KnowledgeRevalidationRequest) -> dict[str, str]:
    return {
        "revalidation_id": str(request.revalidation_id),
        "knowledge_id": request.knowledge_id.to_str(),
        "requested_at": (
            request.requested_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        ),
        "source": request.source,
    }


def _parse_result(payload: Mapping[str, object]) -> KnowledgeRevalidation:
    raw = payload.get("result")
    if not isinstance(raw, Mapping):
        raise KnowledgeValidationError("revalidation result payload must be an object")
    copied: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise KnowledgeValidationError(
                "revalidation result has a non-string key"
            )
        copied[key] = value
    return KnowledgeRevalidation.from_dict(copied)


class KnowledgeAssuranceLedger:
    """Bounded, restart-safe assurance history composed over canonical stores."""

    def __init__(
        self,
        *,
        journal: EventJournal,
        knowledge_store: KnowledgeStore,
        max_replay_events: int = _DEFAULT_MAX_REPLAY_EVENTS,
    ) -> None:
        if not isinstance(journal, EventJournal):
            raise TypeError("journal must be an EventJournal")
        if not isinstance(knowledge_store, KnowledgeStore):
            raise TypeError("knowledge_store must be a KnowledgeStore")
        if (
            not isinstance(max_replay_events, int)
            or isinstance(max_replay_events, bool)
            or max_replay_events <= 0
        ):
            raise ValueError("max_replay_events must be a positive integer")
        self._journal = journal
        self._knowledge_store = knowledge_store
        self._max_replay_events = max_replay_events
        self._lock = RLock()

    def _history(self) -> tuple[Event, ...]:
        entries = self._journal.read(limit=self._max_replay_events + 1)
        if len(entries) > self._max_replay_events:
            raise KnowledgeAssuranceHistoryTooLargeError(
                "event journal exceeds the configured bounded assurance replay window"
            )
        return tuple(entry.event for entry in entries)

    def record_seed(
        self,
        metadata: KnowledgeAssuranceMetadata,
        *,
        recorded_at: datetime | None = None,
    ) -> int:
        """Append explicit source/freshness/evidence metadata without promotion."""
        if not isinstance(metadata, KnowledgeAssuranceMetadata):
            raise TypeError("metadata must be KnowledgeAssuranceMetadata")
        if self._knowledge_store.get(metadata.knowledge_id) is None:
            raise KnowledgeNotFoundError(
                f"No stored knowledge record {metadata.knowledge_id}"
            )
        event = Event.create(
            event_type=EventType.OBSERVATION_RECORDED,
            source=_SOURCE,
            payload=ObservationPayload(
                value={_KIND_KEY: _KIND_SEED, "metadata": metadata.to_dict()}
            ),
            timestamp=recorded_at,
        )
        with self._lock:
            return self._journal.append(event)

    def request_revalidation(self, request: KnowledgeRevalidationRequest) -> int:
        """Persist the requested stage before evidence gathering occurs."""
        if not isinstance(request, KnowledgeRevalidationRequest):
            raise TypeError("request must be KnowledgeRevalidationRequest")
        if self._knowledge_store.get(request.knowledge_id) is None:
            raise KnowledgeNotFoundError(
                f"No stored knowledge record {request.knowledge_id}"
            )
        event = Event(
            event_id=_request_event_id(request.revalidation_id),
            event_type=EventType.OBSERVATION_RECORDED,
            timestamp=request.requested_at,
            schema_version=CURRENT_EVENT_SCHEMA_VERSION,
            source=_SOURCE,
            correlation_id=request.revalidation_id,
            causation_id=None,
            task_id=None,
            payload=ObservationPayload(
                value={_KIND_KEY: _KIND_REQUEST, "request": _request_dict(request)}
            ),
            metadata={},
        )
        with self._lock:
            try:
                return self._journal.append(event)
            except DuplicateEventError as exc:
                raise DuplicateKnowledgeRevalidationError(
                    f"Revalidation {request.revalidation_id} was already requested"
                ) from exc

    def record_revalidation(self, result: KnowledgeRevalidation) -> int:
        """Append one completed result after proving its persisted request exists."""
        if not isinstance(result, KnowledgeRevalidation):
            raise TypeError("result must be KnowledgeRevalidation")
        if self._knowledge_store.get(result.knowledge_id) is None:
            raise KnowledgeNotFoundError(
                f"No stored knowledge record {result.knowledge_id}"
            )

        with self._lock:
            requests = {
                request.revalidation_id: request
                for request in self.list_requests(result.knowledge_id)
            }
            request = requests.get(result.revalidation_id)
            if request is None or request.knowledge_id != result.knowledge_id:
                raise KnowledgeRevalidationNotRequestedError(
                    f"Revalidation {result.revalidation_id} has no persisted matching request"
                )
            event = Event(
                event_id=result.revalidation_id,
                event_type=EventType.OBSERVATION_RECORDED,
                timestamp=result.completed_at,
                schema_version=CURRENT_EVENT_SCHEMA_VERSION,
                source=_SOURCE,
                correlation_id=result.revalidation_id,
                causation_id=_request_event_id(result.revalidation_id),
                task_id=None,
                payload=ObservationPayload(
                    value={_KIND_KEY: _KIND_RESULT, "result": result.to_dict()}
                ),
                metadata={},
            )
            try:
                return self._journal.append(event)
            except DuplicateEventError as exc:
                raise DuplicateKnowledgeRevalidationError(
                    f"Revalidation {result.revalidation_id} was already completed"
                ) from exc

    def list_requests(
        self,
        knowledge_id: KnowledgeId,
    ) -> tuple[KnowledgeRevalidationRequest, ...]:
        if not isinstance(knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be a KnowledgeId")
        values: list[KnowledgeRevalidationRequest] = []
        for event in self._history():
            payload = _payload_mapping(event)
            if payload is None or _kind(payload) != _KIND_REQUEST:
                continue
            request = _parse_request(payload)
            if request.knowledge_id == knowledge_id:
                values.append(request)
        return tuple(values)

    def list_revalidations(
        self,
        knowledge_id: KnowledgeId,
    ) -> tuple[KnowledgeRevalidation, ...]:
        if not isinstance(knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be a KnowledgeId")
        values: list[KnowledgeRevalidation] = []
        for event in self._history():
            payload = _payload_mapping(event)
            if payload is None or _kind(payload) != _KIND_RESULT:
                continue
            result = _parse_result(payload)
            if result.knowledge_id == knowledge_id:
                values.append(result)
        return tuple(values)

    def assurance_for(
        self,
        knowledge_id: KnowledgeId,
        *,
        now: datetime | None = None,
    ) -> KnowledgeAssuranceMetadata:
        """Reconstruct current evidence state deterministically from durable history."""
        if not isinstance(knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be a KnowledgeId")
        record = self._knowledge_store.get(knowledge_id)
        if record is None:
            raise KnowledgeNotFoundError(f"No stored knowledge record {knowledge_id}")

        current = KnowledgeAssuranceMetadata(
            knowledge_id=knowledge_id,
            source_observed_at=record.created_at,
        )
        for event in self._history():
            payload = _payload_mapping(event)
            if payload is None:
                continue
            event_kind = _kind(payload)
            if event_kind == _KIND_SEED:
                seed = _parse_seed(payload)
                if seed.knowledge_id == knowledge_id:
                    current = KnowledgeAssuranceMetadata(
                        knowledge_id=knowledge_id,
                        source_observed_at=seed.source_observed_at,
                        verification_count=current.verification_count,
                        failure_count=current.failure_count,
                        environment_valid=seed.environment_valid,
                        fresh_until=seed.fresh_until,
                        last_verification=current.last_verification,
                        evidence=_merge_evidence(current.evidence, seed.evidence),
                        confidence_state=current.confidence_state,
                        contradiction_count=current.contradiction_count,
                        superseded=current.superseded,
                    )
            elif event_kind == _KIND_RESULT:
                result = _parse_result(payload)
                if result.knowledge_id == knowledge_id:
                    current = assurance_after_revalidation(current, result)

        contradictions = sum(
            1
            for relation in self._knowledge_store.list_contradictions()
            if knowledge_id
            in (relation.first_knowledge_id, relation.second_knowledge_id)
        )
        superseded = any(
            relation.superseded_knowledge_id == knowledge_id
            for relation in self._knowledge_store.list_supersessions()
        )
        state = current.confidence_state
        if superseded:
            state = KnowledgeConfidenceState.SUPERSEDED
        elif contradictions:
            state = KnowledgeConfidenceState.CONFLICTED
        else:
            current_time = datetime.now(UTC) if now is None else now
            if not current.is_fresh_at(current_time):
                state = KnowledgeConfidenceState.STALE

        return KnowledgeAssuranceMetadata(
            knowledge_id=knowledge_id,
            source_observed_at=current.source_observed_at,
            verification_count=current.verification_count,
            failure_count=current.failure_count,
            environment_valid=current.environment_valid,
            fresh_until=current.fresh_until,
            last_verification=current.last_verification,
            evidence=current.evidence,
            confidence_state=state,
            contradiction_count=contradictions,
            superseded=superseded,
        )
