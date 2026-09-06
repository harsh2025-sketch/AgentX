"""C5.04 deterministic selection over canonical C5.03 DOM snapshot data.

This module consumes an already-observed ``BrowserDomObservation`` and returns
structured selection-result data. It performs no browser access, transport,
navigation, JavaScript execution, DOM mutation, action, verification, model
call, persistence, or authority change.

Selectors use exact canonical facts only. Webpage-derived role/name/attribute
strings remain untrusted inert data. A unique match identifies exactly one
snapshot node; it does not imply liveness, interactability, safety,
authorization, or verification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.browser_dom import (
    BrowserDomAttribute,
    BrowserDomNodeRef,
    BrowserDomNodeSnapshot,
    BrowserDomObservation,
)

__all__ = [
    "BROWSER_DOM_SELECTION_SCHEMA_VERSION",
    "BrowserDomSelectionError",
    "BrowserDomSelectionResult",
    "BrowserDomSelectionStatus",
    "BrowserDomSelector",
    "BrowserDomSelectorKind",
    "select_dom_nodes",
]

BROWSER_DOM_SELECTION_SCHEMA_VERSION: Final[int] = 1


class BrowserDomSelectionError(ValueError):
    """Raised when deterministic DOM selection input/result data is invalid."""


class BrowserDomSelectorKind(StrEnum):
    """Closed C5.04 selector vocabulary."""

    NODE = "node"
    TAG = "tag"
    ROLE = "role"
    ACCESSIBLE_NAME = "accessible_name"
    ATTRIBUTE = "attribute"


class BrowserDomSelectionStatus(StrEnum):
    """Explicit ambiguity outcome for deterministic DOM selection."""

    NO_MATCH = "no_match"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class BrowserDomSelector:
    """One exact selector criterion over canonical C5.03 node facts.

    Exactly one criterion must be supplied. Criteria are never combined,
    scored, case-folded, fuzzed, regex-matched, embedded, or interpreted by a
    model.
    """

    node: BrowserDomNodeRef | None = None
    tag: str | None = None
    role: str | None = None
    accessible_name: str | None = None
    attribute: BrowserDomAttribute | None = None

    def __post_init__(self) -> None:
        criteria = (
            self.node,
            self.tag,
            self.role,
            self.accessible_name,
            self.attribute,
        )
        if sum(value is not None for value in criteria) != 1:
            raise BrowserDomSelectionError("exactly one DOM selector criterion must be provided")

        if self.node is not None and not isinstance(self.node, BrowserDomNodeRef):
            raise TypeError(
                f"node must be a BrowserDomNodeRef or None, got {type(self.node).__name__}"
            )
        for field_name, value in (
            ("tag", self.tag),
            ("role", self.role),
            ("accessible_name", self.accessible_name),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string or None, got {type(value).__name__}"
                )
        if self.attribute is not None and not isinstance(self.attribute, BrowserDomAttribute):
            raise TypeError(
                "attribute must be a BrowserDomAttribute or None, "
                f"got {type(self.attribute).__name__}"
            )

    @property
    def kind(self) -> BrowserDomSelectorKind:
        if self.node is not None:
            return BrowserDomSelectorKind.NODE
        if self.tag is not None:
            return BrowserDomSelectorKind.TAG
        if self.role is not None:
            return BrowserDomSelectorKind.ROLE
        if self.accessible_name is not None:
            return BrowserDomSelectorKind.ACCESSIBLE_NAME
        return BrowserDomSelectorKind.ATTRIBUTE

    def to_dict(self) -> dict[str, object]:
        """Return deterministic selector data without execution hooks."""
        if self.node is not None:
            value: object = self.node.to_dict()
        elif self.tag is not None:
            value = self.tag
        elif self.role is not None:
            value = self.role
        elif self.accessible_name is not None:
            value = self.accessible_name
        else:
            assert self.attribute is not None
            value = self.attribute.to_dict()
        return {"kind": self.kind.value, "value": value}

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _validate_selector_scope(
    observation: BrowserDomObservation,
    selector: BrowserDomSelector,
) -> None:
    if selector.node is None:
        return
    if selector.node.target != observation.target:
        raise BrowserDomSelectionError(
            "exact-node selector must belong to the exact canonical source target snapshot"
        )


def _node_matches(
    node: BrowserDomNodeSnapshot,
    selector: BrowserDomSelector,
) -> bool:
    if selector.node is not None:
        return node.node == selector.node
    if selector.tag is not None:
        return node.tag_name == selector.tag
    if selector.role is not None:
        return node.role == selector.role
    if selector.accessible_name is not None:
        return node.name == selector.accessible_name
    assert selector.attribute is not None
    return selector.attribute in node.attributes


def _matching_nodes(
    observation: BrowserDomObservation,
    selector: BrowserDomSelector,
) -> tuple[BrowserDomNodeSnapshot, ...]:
    _validate_selector_scope(observation, selector)
    return tuple(node for node in observation.nodes if _node_matches(node, selector))


def _status_for_count(count: int) -> BrowserDomSelectionStatus:
    if count == 0:
        return BrowserDomSelectionStatus.NO_MATCH
    if count == 1:
        return BrowserDomSelectionStatus.UNIQUE
    return BrowserDomSelectionStatus.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class BrowserDomSelectionResult:
    """Immutable selection outcome bound to one exact C5.03 observation.

    ``matches`` preserves source observation order. ``UNIQUE`` is only a
    cardinality fact and confers no action, safety, interaction, verification,
    or governance authority.
    """

    observation: BrowserDomObservation
    selector: BrowserDomSelector
    status: BrowserDomSelectionStatus
    matches: tuple[BrowserDomNodeSnapshot, ...]
    schema_version: int = BROWSER_DOM_SELECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.observation, BrowserDomObservation):
            raise TypeError(
                "observation must be a BrowserDomObservation, "
                f"got {type(self.observation).__name__}"
            )
        if not isinstance(self.selector, BrowserDomSelector):
            raise TypeError(
                f"selector must be a BrowserDomSelector, got {type(self.selector).__name__}"
            )
        if not isinstance(self.status, BrowserDomSelectionStatus):
            raise TypeError(
                f"status must be a BrowserDomSelectionStatus, got {type(self.status).__name__}"
            )
        if not isinstance(self.matches, tuple):
            raise TypeError("matches must be a tuple")
        for match in self.matches:
            if not isinstance(match, BrowserDomNodeSnapshot):
                raise TypeError(
                    "matches must contain BrowserDomNodeSnapshot values, "
                    f"got {type(match).__name__}"
                )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != BROWSER_DOM_SELECTION_SCHEMA_VERSION:
            raise BrowserDomSelectionError(
                f"unsupported browser DOM selection schema version {self.schema_version}; "
                f"supported version is {BROWSER_DOM_SELECTION_SCHEMA_VERSION}"
            )

        expected = _matching_nodes(self.observation, self.selector)
        if self.matches != expected:
            raise BrowserDomSelectionError(
                "matches must exactly equal deterministic source-order selector matches"
            )
        expected_status = _status_for_count(len(expected))
        if self.status is not expected_status:
            raise BrowserDomSelectionError(
                "status must exactly reflect deterministic match cardinality"
            )

    @property
    def unique_match(self) -> BrowserDomNodeSnapshot | None:
        """Return the sole match when cardinality is exactly one."""
        if self.status is BrowserDomSelectionStatus.UNIQUE:
            return self.matches[0]
        return None

    def to_dict(self) -> dict[str, object]:
        """Serialize source provenance, selector, ambiguity, and exact matches."""
        return {
            "schema_version": self.schema_version,
            "observation": self.observation.to_dict(),
            "selector": self.selector.to_dict(),
            "status": self.status.value,
            "matches": [match.to_dict() for match in self.matches],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def select_dom_nodes(
    observation: BrowserDomObservation,
    selector: BrowserDomSelector,
) -> BrowserDomSelectionResult:
    """Select exact matching snapshot nodes without browser access or policy.

    Zero matches are returned explicitly as ``NO_MATCH``. One match is
    ``UNIQUE``. More than one match is ``AMBIGUOUS``. No tie-breaking,
    visibility preference, score, fuzzy matching, model, or browser refresh is
    performed.
    """
    if not isinstance(observation, BrowserDomObservation):
        raise TypeError(
            f"observation must be a BrowserDomObservation, got {type(observation).__name__}"
        )
    if not isinstance(selector, BrowserDomSelector):
        raise TypeError(f"selector must be a BrowserDomSelector, got {type(selector).__name__}")

    matches = _matching_nodes(observation, selector)
    return BrowserDomSelectionResult(
        observation=observation,
        selector=selector,
        status=_status_for_count(len(matches)),
        matches=matches,
    )
