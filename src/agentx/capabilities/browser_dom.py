"""C5.03 provider-neutral read-only browser DOM observation boundary.

The contracts in this module describe structured DOM observations for an
already-known canonical C5.02 browser target. They do not launch or connect to
a browser, perform network I/O, navigate, execute JavaScript, mutate DOM state,
or perform any browser action.

Every observation is an immutable snapshot. ``AVAILABLE``/``OBSERVED`` values
record provider-supplied state at the boundary; they do not prove later
liveness, authorize interaction, or bypass canonical AgentX governance.
Webpage-derived strings are untrusted inert data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol

from agentx.capabilities.browser_connection import (
    BrowserConnectionState,
    BrowserSessionId,
    BrowserTargetId,
    BrowserTargetRef,
    BrowserTargetState,
)
from agentx.capabilities.browser_provider import BrowserProviderId

__all__ = [
    "BROWSER_DOM_OBSERVATION_SCHEMA_VERSION",
    "CANONICAL_BROWSER_DOM_NODE_STATES",
    "CANONICAL_BROWSER_DOM_OBSERVATION_STATES",
    "BrowserDomAttribute",
    "BrowserDomNodeId",
    "BrowserDomNodeRef",
    "BrowserDomNodeSnapshot",
    "BrowserDomNodeState",
    "BrowserDomObservation",
    "BrowserDomObservationState",
    "BrowserDomReadRequest",
    "BrowserDomReader",
    "BrowserDomValidationError",
]

BROWSER_DOM_OBSERVATION_SCHEMA_VERSION: Final[int] = 1
_MAX_NODE_ID_LENGTH: Final[int] = 512
_MAX_DOCUMENT_VERSION_LENGTH: Final[int] = 512
_MAX_ATTRIBUTE_NAME_LENGTH: Final[int] = 512
_MAX_ATTRIBUTE_VALUE_LENGTH: Final[int] = 65_536
_MAX_TAG_NAME_LENGTH: Final[int] = 512
_MAX_ROLE_LENGTH: Final[int] = 4_096
_MAX_ACCESSIBLE_NAME_LENGTH: Final[int] = 16_384
_MAX_TEXT_LENGTH: Final[int] = 1_048_576
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class BrowserDomValidationError(ValueError):
    """Raised when a C5.03 DOM read-boundary value is malformed."""


def _validate_opaque_id(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise BrowserDomValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise BrowserDomValidationError(f"{field_name} must not exceed {max_length} characters")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise BrowserDomValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_untrusted_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
    allow_none: bool = True,
) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise TypeError(f"{field_name} must be a string")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None, got {type(value).__name__}")
    if len(value) > max_length:
        raise BrowserDomValidationError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_bool_fact(value: object, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool or None, got {type(value).__name__}")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise BrowserDomValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True, order=True)
class BrowserDomNodeId:
    """Opaque deterministic node identity scoped to one canonical browser target."""

    target_id: BrowserTargetId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, BrowserTargetId):
            raise TypeError(
                f"target_id must be a BrowserTargetId, got {type(self.target_id).__name__}"
            )
        _validate_opaque_id(
            self.value,
            field_name="browser DOM node id",
            max_length=_MAX_NODE_ID_LENGTH,
        )

    @property
    def provider_id(self) -> BrowserProviderId:
        """Return the canonical C5.01 provider identity owning this node."""
        return self.target_id.provider_id

    @property
    def session_id(self) -> BrowserSessionId:
        """Return the canonical C5.02 session identity owning this node."""
        return self.target_id.session_id

    def to_dict(self) -> dict[str, object]:
        """Return deterministic full browser identity provenance for this node."""
        return {
            "provider_id": str(self.provider_id),
            "session_id": self.session_id.value,
            "target_id": self.target_id.value,
            "node_id": self.value,
        }

    def __str__(self) -> str:
        return self.value


class BrowserDomNodeState(StrEnum):
    """Provider-supplied node-reference state; never interaction authority."""

    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


CANONICAL_BROWSER_DOM_NODE_STATES: Final[tuple[BrowserDomNodeState, ...]] = (
    BrowserDomNodeState.AVAILABLE,
    BrowserDomNodeState.STALE,
    BrowserDomNodeState.UNAVAILABLE,
)


@dataclass(frozen=True, slots=True)
class BrowserDomNodeRef:
    """Immutable target-scoped node reference snapshot.

    The reference contains no live CDP/DOM handle. ``AVAILABLE`` means only that
    the provider supplied that state for this snapshot. A later browser change
    may make the node stale immediately.
    """

    target: BrowserTargetRef
    node_id: BrowserDomNodeId
    state: BrowserDomNodeState

    def __post_init__(self) -> None:
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError(f"target must be a BrowserTargetRef, got {type(self.target).__name__}")
        if not isinstance(self.node_id, BrowserDomNodeId):
            raise TypeError(
                f"node_id must be a BrowserDomNodeId, got {type(self.node_id).__name__}"
            )
        if self.node_id.target_id != self.target.target_id:
            raise BrowserDomValidationError(
                "node_id must belong to the exact canonical provider/session/target in target"
            )
        if not isinstance(self.state, BrowserDomNodeState):
            raise TypeError(f"state must be a BrowserDomNodeState, got {type(self.state).__name__}")
        if (
            self.state is BrowserDomNodeState.AVAILABLE
            and self.target.state is not BrowserTargetState.AVAILABLE
        ):
            raise BrowserDomValidationError(
                "an available DOM node requires an available browser target snapshot"
            )

    @property
    def provider_id(self) -> BrowserProviderId:
        return self.target.provider_id

    @property
    def session_id(self) -> BrowserSessionId:
        return self.target.session_id

    @property
    def target_id(self) -> BrowserTargetId:
        return self.target.target_id

    def to_dict(self) -> dict[str, object]:
        return {
            **self.node_id.to_dict(),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True, order=True)
class BrowserDomAttribute:
    """One untrusted DOM attribute name/value pair."""

    name: str
    value: str

    def __post_init__(self) -> None:
        _validate_untrusted_text(
            self.name,
            field_name="DOM attribute name",
            max_length=_MAX_ATTRIBUTE_NAME_LENGTH,
            allow_none=False,
        )
        if not self.name:
            raise BrowserDomValidationError("DOM attribute name must not be empty")
        _validate_untrusted_text(
            self.value,
            field_name="DOM attribute value",
            max_length=_MAX_ATTRIBUTE_VALUE_LENGTH,
            allow_none=False,
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class BrowserDomNodeSnapshot:
    """Structured, inert DOM node facts captured by a future read provider.

    Child ordering is preserved because DOM child order is meaningful.
    Attribute ordering is normalized by exact attribute name/value because
    attribute order is not semantic. Visibility and interactability are
    optional provider facts only; neither authorizes a browser action.
    """

    node: BrowserDomNodeRef
    tag_name: str | None = None
    role: str | None = None
    name: str | None = None
    text: str | None = None
    attributes: tuple[BrowserDomAttribute, ...] = ()
    parent_id: BrowserDomNodeId | None = None
    child_ids: tuple[BrowserDomNodeId, ...] = ()
    visible: bool | None = None
    interactable: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node, BrowserDomNodeRef):
            raise TypeError(f"node must be a BrowserDomNodeRef, got {type(self.node).__name__}")
        _validate_untrusted_text(
            self.tag_name,
            field_name="DOM tag name",
            max_length=_MAX_TAG_NAME_LENGTH,
        )
        _validate_untrusted_text(self.role, field_name="DOM role", max_length=_MAX_ROLE_LENGTH)
        _validate_untrusted_text(
            self.name,
            field_name="DOM accessible name",
            max_length=_MAX_ACCESSIBLE_NAME_LENGTH,
        )
        _validate_untrusted_text(self.text, field_name="DOM text", max_length=_MAX_TEXT_LENGTH)
        _validate_bool_fact(self.visible, field_name="visible")
        _validate_bool_fact(self.interactable, field_name="interactable")

        for attribute in self.attributes:
            if not isinstance(attribute, BrowserDomAttribute):
                raise TypeError(
                    "attributes must contain BrowserDomAttribute values, "
                    f"got {type(attribute).__name__}"
                )
        attribute_names = [attribute.name for attribute in self.attributes]
        if len(attribute_names) != len(set(attribute_names)):
            raise BrowserDomValidationError("DOM attribute names must be unique within a node")
        object.__setattr__(
            self,
            "attributes",
            tuple(sorted(self.attributes, key=lambda item: (item.name, item.value))),
        )

        if self.parent_id is not None:
            if not isinstance(self.parent_id, BrowserDomNodeId):
                raise TypeError(
                    "parent_id must be a BrowserDomNodeId or None, "
                    f"got {type(self.parent_id).__name__}"
                )
            self._validate_related_node(self.parent_id, relation="parent")
            if self.parent_id == self.node.node_id:
                raise BrowserDomValidationError("a DOM node cannot be its own parent")

        seen_children: set[BrowserDomNodeId] = set()
        for child_id in self.child_ids:
            if not isinstance(child_id, BrowserDomNodeId):
                raise TypeError(
                    f"child_ids must contain BrowserDomNodeId values, got {type(child_id).__name__}"
                )
            self._validate_related_node(child_id, relation="child")
            if child_id == self.node.node_id:
                raise BrowserDomValidationError("a DOM node cannot be its own child")
            if child_id in seen_children:
                raise BrowserDomValidationError(
                    "child_ids must not contain duplicate node identities"
                )
            seen_children.add(child_id)

    def _validate_related_node(self, node_id: BrowserDomNodeId, *, relation: str) -> None:
        if node_id.target_id != self.node.target_id:
            raise BrowserDomValidationError(
                f"{relation} node must belong to the exact same browser target"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "node": self.node.to_dict(),
            "tag_name": self.tag_name,
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "attributes": [attribute.to_dict() for attribute in self.attributes],
            "parent_id": None if self.parent_id is None else self.parent_id.to_dict(),
            "child_ids": [child_id.to_dict() for child_id in self.child_ids],
            "visible": self.visible,
            "interactable": self.interactable,
        }


class BrowserDomObservationState(StrEnum):
    """Explicit DOM snapshot state supplied by a future read implementation."""

    OBSERVED = "observed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


CANONICAL_BROWSER_DOM_OBSERVATION_STATES: Final[tuple[BrowserDomObservationState, ...]] = (
    BrowserDomObservationState.OBSERVED,
    BrowserDomObservationState.STALE,
    BrowserDomObservationState.UNAVAILABLE,
)


@dataclass(frozen=True, slots=True)
class BrowserDomReadRequest:
    """Read-only request for structured DOM state from one canonical target.

    Construction checks only C5.02 snapshot eligibility. It does not grant
    Permission or prove the target is still live when a future provider uses it.
    """

    target: BrowserTargetRef

    def __post_init__(self) -> None:
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError(f"target must be a BrowserTargetRef, got {type(self.target).__name__}")
        if self.target.connection.state is not BrowserConnectionState.CONNECTED:
            raise BrowserDomValidationError(
                "a DOM read request requires a connected connection snapshot"
            )
        if self.target.state is not BrowserTargetState.AVAILABLE:
            raise BrowserDomValidationError(
                "a DOM read request requires an available target snapshot"
            )

    def to_dict(self) -> dict[str, object]:
        return {"target": self.target.to_dict()}

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class BrowserDomObservation:
    """Immutable structured DOM observation snapshot for one canonical target."""

    target: BrowserTargetRef
    state: BrowserDomObservationState
    observed_at: datetime
    nodes: tuple[BrowserDomNodeSnapshot, ...] = ()
    root_id: BrowserDomNodeId | None = None
    document_version: str | None = None
    schema_version: int = BROWSER_DOM_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.target, BrowserTargetRef):
            raise TypeError(f"target must be a BrowserTargetRef, got {type(self.target).__name__}")
        if not isinstance(self.state, BrowserDomObservationState):
            raise TypeError(
                f"state must be a BrowserDomObservationState, got {type(self.state).__name__}"
            )
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        if self.document_version is not None:
            _validate_opaque_id(
                self.document_version,
                field_name="DOM document version",
                max_length=_MAX_DOCUMENT_VERSION_LENGTH,
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != BROWSER_DOM_OBSERVATION_SCHEMA_VERSION:
            raise BrowserDomValidationError(
                f"unsupported browser DOM observation schema version {self.schema_version}; "
                f"supported version is {BROWSER_DOM_OBSERVATION_SCHEMA_VERSION}"
            )

        if (
            self.state is BrowserDomObservationState.OBSERVED
            and self.target.state is not BrowserTargetState.AVAILABLE
        ):
            raise BrowserDomValidationError(
                "an observed DOM snapshot requires an available target snapshot"
            )
        if self.state is BrowserDomObservationState.UNAVAILABLE and (self.nodes or self.root_id):
            raise BrowserDomValidationError(
                "an unavailable DOM observation cannot contain root or node snapshots"
            )

        node_ids: set[BrowserDomNodeId] = set()
        for node in self.nodes:
            if not isinstance(node, BrowserDomNodeSnapshot):
                raise TypeError(
                    f"nodes must contain BrowserDomNodeSnapshot values, got {type(node).__name__}"
                )
            if node.node.target != self.target:
                raise BrowserDomValidationError(
                    "every DOM node snapshot must belong to the exact same target snapshot"
                )
            if node.node.node_id in node_ids:
                raise BrowserDomValidationError("DOM observation nodes must have unique identities")
            node_ids.add(node.node.node_id)

        if self.root_id is not None:
            if not isinstance(self.root_id, BrowserDomNodeId):
                raise TypeError(
                    f"root_id must be a BrowserDomNodeId or None, got {type(self.root_id).__name__}"
                )
            if self.root_id.target_id != self.target.target_id:
                raise BrowserDomValidationError(
                    "root_id must belong to the exact canonical browser target"
                )
            if self.root_id not in node_ids:
                raise BrowserDomValidationError(
                    "root_id must identify a node present in the observation"
                )

    @property
    def provider_id(self) -> BrowserProviderId:
        return self.target.provider_id

    @property
    def session_id(self) -> BrowserSessionId:
        return self.target.session_id

    @property
    def target_id(self) -> BrowserTargetId:
        return self.target.target_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "target": self.target.to_dict(),
            "state": self.state.value,
            "observed_at": _format_timestamp(self.observed_at),
            "document_version": self.document_version,
            "root_id": None if self.root_id is None else self.root_id.to_dict(),
            "nodes": [node.to_dict() for node in self.nodes],
        }

    def to_json(self) -> str:
        """Serialize deterministically without dynamic-code or browser hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class BrowserDomReader(Protocol):
    """Provider port for future read-only structured DOM implementations.

    Implementations may obtain provider-specific structured observations, but
    this protocol itself performs no I/O and exposes no browser mutation method.
    Returning an observation does not authorize any subsequent browser action.
    """

    def observe_dom(self, request: BrowserDomReadRequest) -> BrowserDomObservation:
        """Return one provider-supplied immutable DOM snapshot."""
        ...
