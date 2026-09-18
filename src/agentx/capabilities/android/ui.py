"""Typed Android UI hierarchy and semantic target resolution for M13."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Final

__all__ = [
    "AndroidBounds",
    "AndroidTargetSelector",
    "AndroidUiNode",
    "AndroidUiTree",
    "AndroidUiValidationError",
    "parse_android_ui_tree",
    "resolve_android_target",
]

_MAX_UI_XML_BYTES: Final[int] = 2 * 1_048_576
_MAX_UI_NODES: Final[int] = 4096
_MAX_UI_TEXT: Final[int] = 4096
_BOUNDS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]"
)


class AndroidUiValidationError(ValueError):
    """Malformed or ambiguous Android semantic UI evidence."""


def _bounded_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    if len(value) > _MAX_UI_TEXT:
        raise AndroidUiValidationError(f"{field_name} exceeds bounded length")
    return value


def _bool_attr(value: str | None) -> bool:
    return value == "true"


@dataclass(frozen=True, slots=True, order=True)
class AndroidBounds:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        for name, value in (
            ("left", self.left),
            ("top", self.top),
            ("right", self.right),
            ("bottom", self.bottom),
        ):
            if type(value) is not int or value < 0:
                raise AndroidUiValidationError(f"{name} must be a non-negative int")
        if self.right <= self.left or self.bottom <= self.top:
            raise AndroidUiValidationError("bounds must have positive width and height")

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True, slots=True)
class AndroidUiNode:
    resource_id: str
    text: str
    content_description: str
    class_name: str
    package: str
    bounds: AndroidBounds
    enabled: bool
    clickable: bool
    focusable: bool
    visible: bool

    def __post_init__(self) -> None:
        for field_name in (
            "resource_id",
            "text",
            "content_description",
            "class_name",
            "package",
        ):
            _bounded_text(getattr(self, field_name), field_name=field_name)
        if not isinstance(self.bounds, AndroidBounds):
            raise TypeError("bounds must be AndroidBounds")
        for field_name in ("enabled", "clickable", "focusable", "visible"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be bool")


@dataclass(frozen=True, slots=True)
class AndroidUiTree:
    nodes: tuple[AndroidUiNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple):
            raise TypeError("nodes must be a tuple")
        if len(self.nodes) > _MAX_UI_NODES:
            raise AndroidUiValidationError("UI hierarchy exceeds bounded node count")
        for node in self.nodes:
            if not isinstance(node, AndroidUiNode):
                raise TypeError("nodes must contain AndroidUiNode values")


@dataclass(frozen=True, slots=True)
class AndroidTargetSelector:
    """Exact semantic selector. At least one semantic field is mandatory."""

    resource_id: str | None = None
    text: str | None = None
    content_description: str | None = None
    class_name: str | None = None
    package: str | None = None
    require_enabled: bool = True
    require_clickable: bool = False
    require_visible: bool = True

    def __post_init__(self) -> None:
        values = (
            self.resource_id,
            self.text,
            self.content_description,
            self.class_name,
            self.package,
        )
        if all(value is None for value in values):
            raise AndroidUiValidationError("semantic selector requires at least one exact field")
        for field_name, value in (
            ("resource_id", self.resource_id),
            ("text", self.text),
            ("content_description", self.content_description),
            ("class_name", self.class_name),
            ("package", self.package),
        ):
            if value is not None:
                _bounded_text(value, field_name=field_name)
        for field_name in ("require_enabled", "require_clickable", "require_visible"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be bool")


def parse_android_ui_tree(payload: bytes) -> AndroidUiTree:
    """Parse bounded uiautomator XML without interpreting UI text as instructions."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not payload or len(payload) > _MAX_UI_XML_BYTES:
        raise AndroidUiValidationError("UI hierarchy payload is empty or exceeds safety bound")
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise AndroidUiValidationError("DTD/entity declarations are forbidden")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise AndroidUiValidationError("UI hierarchy is malformed XML") from exc
    nodes: list[AndroidUiNode] = []
    for element in root.iter("node"):
        if len(nodes) >= _MAX_UI_NODES:
            raise AndroidUiValidationError("UI hierarchy exceeds bounded node count")
        bounds_raw = element.attrib.get("bounds", "")
        match = _BOUNDS_PATTERN.fullmatch(bounds_raw)
        if match is None:
            continue
        bounds = AndroidBounds(*(int(group) for group in match.groups()))
        nodes.append(
            AndroidUiNode(
                resource_id=_bounded_text(
                    element.attrib.get("resource-id", ""), field_name="resource_id"
                ),
                text=_bounded_text(element.attrib.get("text", ""), field_name="text"),
                content_description=_bounded_text(
                    element.attrib.get("content-desc", ""),
                    field_name="content_description",
                ),
                class_name=_bounded_text(
                    element.attrib.get("class", ""), field_name="class_name"
                ),
                package=_bounded_text(
                    element.attrib.get("package", ""), field_name="package"
                ),
                bounds=bounds,
                enabled=_bool_attr(element.attrib.get("enabled")),
                clickable=_bool_attr(element.attrib.get("clickable")),
                focusable=_bool_attr(element.attrib.get("focusable")),
                visible=not _bool_attr(element.attrib.get("NAF"))
                and element.attrib.get("visible-to-user", "true") != "false",
            )
        )
    return AndroidUiTree(nodes=tuple(nodes))


def resolve_android_target(
    tree: AndroidUiTree,
    selector: AndroidTargetSelector,
) -> AndroidUiNode:
    """Resolve exactly one semantic target; missing/ambiguous matches fail closed."""
    if not isinstance(tree, AndroidUiTree):
        raise TypeError("tree must be AndroidUiTree")
    if not isinstance(selector, AndroidTargetSelector):
        raise TypeError("selector must be AndroidTargetSelector")

    def matches(node: AndroidUiNode) -> bool:
        if selector.resource_id is not None and node.resource_id != selector.resource_id:
            return False
        if selector.text is not None and node.text != selector.text:
            return False
        if (
            selector.content_description is not None
            and node.content_description != selector.content_description
        ):
            return False
        if selector.class_name is not None and node.class_name != selector.class_name:
            return False
        if selector.package is not None and node.package != selector.package:
            return False
        if selector.require_enabled and not node.enabled:
            return False
        if selector.require_clickable and not node.clickable:
            return False
        return not selector.require_visible or node.visible

    found = tuple(node for node in tree.nodes if matches(node))
    if not found:
        raise AndroidUiValidationError("semantic target was not found")
    if len(found) != 1:
        raise AndroidUiValidationError("semantic target is ambiguous")
    return found[0]
