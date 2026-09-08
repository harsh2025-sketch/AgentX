"""Deterministic semantic target resolution over A5.03 Windows UIA observations (N2.24).

This module answers one selection question from already-observed structured UI
Automation data:

    Which observed UIA element, if any, uniquely satisfies a structured target
    query?

It is a pure data-processing boundary. It does not call UI Automation, does not
obtain or use a pattern object, does not click, invoke, type, set a value,
change focus, mutate windows, call a model, run embeddings, use screenshots, or
perform a visual fallback. It never reads the live desktop and never claims that
a resolved snapshot element still exists now. A5.03 observes; this module
resolves; later capability code may act under normal governance.

Observed UI text is untrusted data. Names, automation IDs, values and hostile
text are stored and compared exactly according to typed query semantics; they
cannot grant permission, change risk, bypass an authority boundary, mark a
target verified, trigger a model call, or otherwise authorize execution.

Owner: N2.24. Belongs to ``agentx.capabilities.windows``; imports only the
standard library, the A5.03 canonical observation contracts, and the canonical
JSON value contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.windows.uia_tree import (
    UIAElementReference,
    UIAElementSnapshot,
    UIAElementState,
    UIATreeSnapshot,
)
from agentx.core.tasks import JsonValue

__all__ = [
    "UIA_TARGET_RESOLUTION_SCHEMA_VERSION",
    "UIATargetQuery",
    "UIATargetResolutionError",
    "UIATargetResolutionResult",
    "UIATargetResolutionStatus",
    "resolve_uia_target",
]

UIA_TARGET_RESOLUTION_SCHEMA_VERSION: Final[int] = 1


class UIATargetResolutionError(ValueError):
    """Raised when UIA target-resolution input or result data is invalid."""


class UIATargetResolutionStatus(StrEnum):
    """Explicit cardinality outcome for deterministic UIA target resolution."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class UIATargetQuery:
    """One structured target query over canonical A5.03 element facts.

    Every supplied field is an exact typed constraint. Unsupplied fields are
    ignored. ``reference`` is the strongest identity and matches by snapshot
    root plus element path, with the optional native runtime ID used as an
    additional exact constraint when present. ``parent_path`` constrains the
    direct parent and ``ancestor_path`` constrains a transitive ancestor of the
    candidate. Boolean requirement fields choose ``True`` (must satisfy) or
    ``False`` (must not satisfy); ``None`` means no requirement.

    No field is fuzzy, ranked, embedded, model-interpreted, regex-matched, or
    natural-language interpreted.
    """

    reference: UIAElementReference | None = None
    automation_id: str | None = None
    control_type: int | None = None
    name: str | None = None
    parent_path: tuple[int, ...] | None = None
    ancestor_path: tuple[int, ...] | None = None
    require_enabled: bool | None = None
    require_visible: bool | None = None
    require_keyboard_focusable: bool | None = None

    def __post_init__(self) -> None:
        if self.reference is not None and not isinstance(self.reference, UIAElementReference):
            raise TypeError(
                f"reference must be a UIAElementReference or None, "
                f"got {type(self.reference).__name__}"
            )
        if self.automation_id is not None and not isinstance(self.automation_id, str):
            raise TypeError(
                f"automation_id must be a string or None, got {type(self.automation_id).__name__}"
            )
        if self.name is not None and not isinstance(self.name, str):
            raise TypeError(f"name must be a string or None, got {type(self.name).__name__}")
        if self.control_type is not None:
            if type(self.control_type) is not int:
                raise TypeError("control_type must be an int or None")
            if self.control_type < 0:
                raise ValueError("control_type must be non-negative")
        if self.parent_path is not None:
            _validate_path(self.parent_path, field_name="parent_path")
        if self.ancestor_path is not None:
            _validate_path(self.ancestor_path, field_name="ancestor_path")
        if self.require_enabled is not None and type(self.require_enabled) is not bool:
            raise TypeError(
                f"require_enabled must be a bool or None, got {type(self.require_enabled).__name__}"
            )
        if self.require_visible is not None and type(self.require_visible) is not bool:
            raise TypeError(
                f"require_visible must be a bool or None, got {type(self.require_visible).__name__}"
            )
        if (
            self.require_keyboard_focusable is not None
            and type(self.require_keyboard_focusable) is not bool
        ):
            raise TypeError(
                "require_keyboard_focusable must be a bool or None, "
                f"got {type(self.require_keyboard_focusable).__name__}"
            )

        supplied = (
            self.reference is not None,
            self.automation_id is not None,
            self.control_type is not None,
            self.name is not None,
            self.parent_path is not None,
            self.ancestor_path is not None,
            self.require_enabled is not None,
            self.require_visible is not None,
            self.require_keyboard_focusable is not None,
        )
        if not any(supplied):
            raise UIATargetResolutionError("at least one structured target criterion is required")

    def to_dict(self) -> dict[str, JsonValue]:
        """Return deterministic JSON-compatible query data."""
        result: dict[str, JsonValue] = {}
        if self.reference is not None:
            result["reference"] = self.reference.to_dict()
        if self.automation_id is not None:
            result["automation_id"] = self.automation_id
        if self.control_type is not None:
            result["control_type"] = self.control_type
        if self.name is not None:
            result["name"] = self.name
        if self.parent_path is not None:
            result["parent_path"] = list(self.parent_path)
        if self.ancestor_path is not None:
            result["ancestor_path"] = list(self.ancestor_path)
        if self.require_enabled is not None:
            result["require_enabled"] = self.require_enabled
        if self.require_visible is not None:
            result["require_visible"] = self.require_visible
        if self.require_keyboard_focusable is not None:
            result["require_keyboard_focusable"] = self.require_keyboard_focusable
        return result

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _validate_path(value: tuple[int, ...], *, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of non-negative ints or None")
    for item in value:
        if type(item) is not int:
            raise TypeError(f"{field_name} entries must be ints")
        if item < 0:
            raise ValueError(f"{field_name} entries must be non-negative")


@dataclass(frozen=True, slots=True)
class UIATargetResolutionResult:
    """Immutable resolution outcome bound to one A5.03 observation snapshot.

    ``matches`` are sorted by canonical element path so the same multiset of
    elements always produces the same result regardless of traversal order.
    ``RESOLVED`` is only a cardinality fact about the supplied snapshot; it
    confers no action, liveness, safety, interaction, verification, or
    governance authority.
    """

    snapshot: UIATreeSnapshot
    query: UIATargetQuery
    status: UIATargetResolutionStatus
    matches: tuple[UIAElementSnapshot, ...]
    schema_version: int = UIA_TARGET_RESOLUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, UIATreeSnapshot):
            raise TypeError(
                f"snapshot must be a UIATreeSnapshot, got {type(self.snapshot).__name__}"
            )
        if not isinstance(self.query, UIATargetQuery):
            raise TypeError(f"query must be a UIATargetQuery, got {type(self.query).__name__}")
        if not isinstance(self.status, UIATargetResolutionStatus):
            raise TypeError(
                f"status must be a UIATargetResolutionStatus, got {type(self.status).__name__}"
            )
        if not isinstance(self.matches, tuple):
            raise TypeError("matches must be a tuple")
        for match in self.matches:
            if not isinstance(match, UIAElementSnapshot):
                raise TypeError("matches must contain UIAElementSnapshot values")
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an int")
        if self.schema_version != UIA_TARGET_RESOLUTION_SCHEMA_VERSION:
            raise UIATargetResolutionError(
                f"unsupported UIA target-resolution schema version {self.schema_version}; "
                f"supported version is {UIA_TARGET_RESOLUTION_SCHEMA_VERSION}"
            )

        expected = _matching_elements(self.snapshot, self.query)
        if self.matches != expected:
            raise UIATargetResolutionError(
                "matches must exactly equal deterministic path-ordered query matches"
            )
        expected_status = _status_for_count(len(expected))
        if self.status is not expected_status:
            raise UIATargetResolutionError(
                "status must exactly reflect deterministic match cardinality"
            )

    @property
    def unique_match(self) -> UIAElementSnapshot | None:
        """Return the sole match when cardinality is exactly one."""
        if self.status is UIATargetResolutionStatus.RESOLVED:
            return self.matches[0]
        return None

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize source observation, query, status and exact matches."""
        return {
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_dict(),
            "query": self.query.to_dict(),
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


def _status_for_count(count: int) -> UIATargetResolutionStatus:
    if count == 0:
        return UIATargetResolutionStatus.NOT_FOUND
    if count == 1:
        return UIATargetResolutionStatus.RESOLVED
    return UIATargetResolutionStatus.AMBIGUOUS


def _reference_matches(
    snapshot: UIATreeSnapshot,
    element: UIAElementSnapshot,
    reference: UIAElementReference,
) -> bool:
    if reference.root_window_handle != snapshot.root_window_handle:
        raise UIATargetResolutionError(
            "exact reference must belong to the supplied A5.03 snapshot root window"
        )
    if element.reference.path != reference.path:
        return False
    if reference.runtime_id is None:
        return True
    return bool(element.reference.runtime_id == reference.runtime_id)


def _ancestor_matches(ancestor: tuple[int, ...], candidate: tuple[int, ...]) -> bool:
    return len(candidate) > len(ancestor) and candidate[: len(ancestor)] == ancestor


def _element_matches(
    snapshot: UIATreeSnapshot,
    element: UIAElementSnapshot,
    query: UIATargetQuery,
) -> bool:
    if element.state is UIAElementState.VANISHED:
        return False
    if query.reference is not None and not _reference_matches(snapshot, element, query.reference):
        return False
    if query.automation_id is not None and element.automation_id != query.automation_id:
        return False
    if query.control_type is not None and element.control_type != query.control_type:
        return False
    if query.name is not None and element.name != query.name:
        return False
    if query.parent_path is not None and element.parent_path != query.parent_path:
        return False
    if query.ancestor_path is not None and not _ancestor_matches(
        query.ancestor_path, element.reference.path
    ):
        return False
    if query.require_enabled is not None and element.is_enabled != query.require_enabled:
        return False
    if query.require_visible is not None and element.is_visible != query.require_visible:
        return False
    return not (
        query.require_keyboard_focusable is not None
        and element.is_keyboard_focusable != query.require_keyboard_focusable
    )


def _matching_elements(
    snapshot: UIATreeSnapshot,
    query: UIATargetQuery,
) -> tuple[UIAElementSnapshot, ...]:
    if not isinstance(snapshot, UIATreeSnapshot):
        raise TypeError(f"snapshot must be a UIATreeSnapshot, got {type(snapshot).__name__}")
    if not isinstance(query, UIATargetQuery):
        raise TypeError(f"query must be a UIATargetQuery, got {type(query).__name__}")
    if (
        query.reference is not None
        and query.reference.root_window_handle != snapshot.root_window_handle
    ):
        raise UIATargetResolutionError(
            "exact reference must belong to the supplied A5.03 snapshot root window"
        )
    matches = [
        element for element in snapshot.elements if _element_matches(snapshot, element, query)
    ]
    return tuple(sorted(matches, key=lambda element: element.reference.path))


def resolve_uia_target(
    snapshot: UIATreeSnapshot,
    query: UIATargetQuery,
) -> UIATargetResolutionResult:
    """Resolve exactly one structured target query within a supplied snapshot.

    Zero matches return ``NOT_FOUND``. One match returns ``RESOLVED``. More
    than one match returns ``AMBIGUOUS``. No tie-breaking, hidden geometry
    preference, visibility preference, score, fuzzy matching, model inference,
    snapshot refresh, or live UIA read is performed.
    """
    if not isinstance(snapshot, UIATreeSnapshot):
        raise TypeError(f"snapshot must be a UIATreeSnapshot, got {type(snapshot).__name__}")
    if not isinstance(query, UIATargetQuery):
        raise TypeError(f"query must be a UIATargetQuery, got {type(query).__name__}")
    matches = _matching_elements(snapshot, query)
    return UIATargetResolutionResult(
        snapshot=snapshot,
        query=query,
        status=_status_for_count(len(matches)),
        matches=matches,
    )
