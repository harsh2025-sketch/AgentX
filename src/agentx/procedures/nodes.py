"""Typed ACTION / OBSERVE / VERIFY node contracts (A3.02).

This module adds *node-family semantics* for exactly three canonical
:class:`~agentx.procedures.graph.ProcedureNodeKind` members on top of the A3.01
Procedure Graph IR:

    - :class:`ActionNodeSpec` — describes a requested capability action.
    - :class:`ObserveNodeSpec` — describes the observation/evidence expected
      after an action.
    - :class:`VerifyNodeSpec` — describes an explicit verification requirement.

Everything here is DATA. The three specs are typed *readings* of the opaque
``ProcedureNode.params`` mapping owned by A3.01; they add no new node kinds, no
new graph structure, and no runtime surface.

Critical invariants
-------------------
    - ACTION describes a requested action. It never executes it, holds no
      permission/authority/risk override, and carries no result, outcome,
      status, or success field of any kind.
    - OBSERVE describes what evidence is *expected*. It never claims that
      evidence was obtained, never asserts that an action succeeded, and
      carries no verdict.
    - VERIFY encodes a verification *requirement*. It never performs a check
      and it cannot mark anything verified: no ``passed``/verdict field exists
      on the spec, by construction.
    - Node ``params`` remain inert. Parsing a spec only validates shapes and
      copies JSON-compatible values; hostile strings inside a description,
      criterion, locator, or parameter value are carried as inert data and are
      never interpreted, resolved, expanded, or dispatched.

Reuse, not competition
----------------------
    - Graph structure is reused verbatim from A3.01
      (``ProcedureGraph``/``ProcedureNode``/``ProcedureNodeKind``); this module
      defines no competing graph or node type.
    - The Capability ABI in ``agentx.capabilities.abi`` remains the single
      canonical capability contract. This module deliberately does NOT define a
      competing capability ABI: an ACTION node records only *request-compatible
      identity data* — a capability name and an explicit ``major.minor.patch``
      version validated with the same rules the canonical
      :class:`~agentx.capabilities.abi.CapabilityIdentity` applies — plus opaque
      JSON parameters. It is not a ``CapabilityRequest``, cannot be executed,
      and binds no typed ``CapabilityParams``; turning a described action into a
      real, typed, kernel-governed request is owned by later tasks.
    - The canonical evidence vocabulary
      (:class:`agentx.core.provenance.EvidenceKind`) is composed rather than
      re-invented for OBSERVE/VERIFY expectations.
    - The canonical verification verdict
      (:class:`agentx.capabilities.abi.VerificationResult`) stays where it is.
      VERIFY nodes describe *what must be verified*; producing a verdict is a
      separate, later concern and is not representable here.

Boundaries
----------
The canonical architecture manifest allows ``agentx.procedures`` to depend on
``agentx.core`` (and, for future runtime work, ``agentx.kernel``) — not on
``agentx.capabilities``. This module therefore imports only the standard
library, A3.01 graph structure, and the ``agentx.core`` evidence vocabulary. It
performs no side effects and reaches no authority, model, capability, task,
event, telemetry, or storage subsystem. Nothing in A3.03-A3.10 (BRANCH /
TRANSFORM / WAIT / REASON / RESEARCH / ROLLBACK / SUBPROCEDURE / END semantics,
pre/postconditions, recovery, interpretation, execution, telemetry) is
implemented here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.provenance import EvidenceKind
from agentx.procedures.graph import (
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "ACTION_PARAM_FIELDS",
    "OBSERVE_PARAM_FIELDS",
    "VERIFY_PARAM_FIELDS",
    "ActionNodeSpec",
    "ObserveNodeSpec",
    "ProcedureNodeContractError",
    "VerificationRequirementStrength",
    "VerifyNodeSpec",
]

# Mirrors the canonical capability-name rule in ``agentx.capabilities.abi`` so
# ACTION data stays request-compatible without importing (or duplicating) the
# capability ABI across an architecture boundary.
_CAPABILITY_NAME_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MAX_NAME_LENGTH: Final[int] = 128
_MAX_SHORT_TEXT_LENGTH: Final[int] = 512
_MAX_LONG_TEXT_LENGTH: Final[int] = 1024
_MAX_VERSION_COMPONENT: Final[int] = (1 << 63) - 1
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

ACTION_PARAM_FIELDS: Final[frozenset[str]] = frozenset(
    {"capability_name", "capability_version", "description", "params"}
)
OBSERVE_PARAM_FIELDS: Final[frozenset[str]] = frozenset(
    {"expectation", "evidence_kind", "locator", "required_fields"}
)
VERIFY_PARAM_FIELDS: Final[frozenset[str]] = frozenset(
    {"requirement", "criterion", "evidence_kind", "strength"}
)


class ProcedureNodeContractError(ProcedureGraphError):
    """Raised when ACTION/OBSERVE/VERIFY node data violates its contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so callers
    can handle every Procedure Graph data failure uniformly; this module adds no
    parallel error hierarchy.
    """


# --------------------------------------------------------------------------
# Shared validation helpers (data shape only; never semantics of the content).
# --------------------------------------------------------------------------


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed, control-character-free text."""
    if not isinstance(value, str):
        raise ProcedureNodeContractError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ProcedureNodeContractError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ProcedureNodeContractError(f"{field_name} must not contain control characters")
    if len(value) > max_length:
        raise ProcedureNodeContractError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_optional_text(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name, max_length=max_length)


def _validate_capability_name(value: object) -> str:
    """Validate a capability name using the canonical capability-name rule.

    A name is descriptive identity only. It grants no authority, does not prove
    that such a capability exists, and is never resolved or looked up here.
    """
    name = _validate_text(value, field_name="action.capability_name", max_length=_MAX_NAME_LENGTH)
    if _CAPABILITY_NAME_PATTERN.fullmatch(name) is None:
        raise ProcedureNodeContractError(
            "action.capability_name must be lowercase alphanumeric segments separated by "
            f"single '._-' characters, got {name!r}"
        )
    return name


def _validate_capability_version(value: object) -> str:
    """Validate an explicit ``major.minor.patch`` version string.

    Versions are structured, never opaque: the same three non-negative integer
    components the canonical capability ABI requires.
    """
    raw = _validate_text(value, field_name="action.capability_version", max_length=_MAX_NAME_LENGTH)
    parts = raw.split(".")
    if len(parts) != 3 or any(not part.isascii() or not part.isdigit() for part in parts):
        raise ProcedureNodeContractError(
            "action.capability_version must have the form 'major.minor.patch' with "
            f"non-negative integer components, got {raw!r}"
        )
    if any(int(part) > _MAX_VERSION_COMPONENT for part in parts):
        raise ProcedureNodeContractError(
            "action.capability_version components exceed the supported range"
        )
    return raw


def _validate_evidence_kind(value: object, *, field_name: str) -> EvidenceKind:
    """Validate a canonical evidence-kind vocabulary member."""
    if isinstance(value, EvidenceKind):
        return value
    if not isinstance(value, str):
        raise ProcedureNodeContractError(f"{field_name} must be an evidence kind string")
    try:
        return EvidenceKind(value)
    except ValueError as exc:
        raise ProcedureNodeContractError(f"unknown evidence kind: {value!r}") from exc


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy.

    Values are copied, never interpreted. A string that looks like an
    instruction, a path, a URL, or a template is stored as an inert string.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ProcedureNodeContractError(f"{path} must be a finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProcedureNodeContractError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, bytes | bytearray):
        raise ProcedureNodeContractError(f"{path} contains an unsupported binary value")
    if isinstance(value, Sequence):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise ProcedureNodeContractError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _thaw_json(value: object) -> object:
    """Return plain JSON-compatible primitives for a frozen value."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_json_object(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProcedureNodeContractError(f"{field_name} must be a mapping")
    frozen = _freeze_json(value, path=field_name)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("JSON object freezing produced a non-mapping")
    return frozen


def _validate_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], field_name: str
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ProcedureNodeContractError(f"{field_name} missing required fields: {sorted(missing)}")
    raise ProcedureNodeContractError(f"{field_name} contains unknown fields: {sorted(unknown)}")


def _params_mapping(raw: Mapping[str, object], *, field_name: str) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ProcedureNodeContractError(f"{field_name} must be a mapping")
    copied: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise ProcedureNodeContractError(f"{field_name} contains a non-string key")
        copied[key] = item
    return copied


def _require_kind(node: object, *, expected: ProcedureNodeKind) -> ProcedureNode:
    if not isinstance(node, ProcedureNode):
        raise ProcedureNodeContractError("node must be a ProcedureNode")
    if node.kind is not expected:
        raise ProcedureNodeContractError(
            f"expected a {expected.value!r} node, got {node.kind.value!r}"
        )
    return node


def _node_id(value: object) -> ProcedureNodeId:
    if isinstance(value, ProcedureNodeId):
        return value
    return ProcedureNodeId.parse(value)


# --------------------------------------------------------------------------
# ACTION.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionNodeSpec:
    """A described, *requested* capability action — never an executed one.

    The spec records request-compatible identity data (``capability_name`` plus
    an explicit ``major.minor.patch`` ``capability_version``) and opaque JSON
    ``params``. It is inert:

        - it does not execute, schedule, dispatch, or authorize anything;
        - it carries no permission, risk classification, budget, or context;
        - it has no result/outcome/status/success field, so an ACTION node can
          never imply — or be mistaken for — a completed or successful action;
        - it is not a canonical ``CapabilityRequest`` and does not replace one.
    """

    capability_name: str
    capability_version: str
    description: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_name", _validate_capability_name(self.capability_name))
        object.__setattr__(
            self, "capability_version", _validate_capability_version(self.capability_version)
        )
        object.__setattr__(
            self,
            "description",
            _validate_optional_text(
                self.description,
                field_name="action.description",
                max_length=_MAX_SHORT_TEXT_LENGTH,
            ),
        )
        object.__setattr__(
            self, "params", _validate_json_object(self.params, field_name="action.params")
        )

    def to_params(self) -> dict[str, object]:
        """Return the canonical JSON-compatible ``ProcedureNode.params`` payload."""
        return {
            "capability_name": self.capability_name,
            "capability_version": self.capability_version,
            "description": self.description,
            "params": _thaw_json(self.params),
        }

    @classmethod
    def from_params(cls, raw: Mapping[str, object]) -> ActionNodeSpec:
        """Parse ACTION semantics out of an opaque node ``params`` mapping."""
        copied = _params_mapping(raw, field_name="action params")
        _validate_exact_fields(copied, expected=ACTION_PARAM_FIELDS, field_name="action params")
        return cls(
            capability_name=_validate_capability_name(copied["capability_name"]),
            capability_version=_validate_capability_version(copied["capability_version"]),
            description=_validate_optional_text(
                copied["description"],
                field_name="action.description",
                max_length=_MAX_SHORT_TEXT_LENGTH,
            ),
            params=_validate_json_object(copied["params"], field_name="action.params"),
        )

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build the corresponding A3.01 ACTION node (structure unchanged)."""
        return ProcedureNode(
            id=_node_id(node_id),
            kind=ProcedureNodeKind.ACTION,
            label=label,
            params=self.to_params(),
        )

    @classmethod
    def from_node(cls, node: ProcedureNode) -> ActionNodeSpec:
        """Read ACTION semantics from an A3.01 node, rejecting other kinds."""
        return cls.from_params(_require_kind(node, expected=ProcedureNodeKind.ACTION).params)


# --------------------------------------------------------------------------
# OBSERVE.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ObserveNodeSpec:
    """The observation/evidence *expected* after an action — never a claim.

    ``expectation`` states, in inert text, what evidence should be gathered.
    ``evidence_kind`` composes the canonical
    :class:`agentx.core.provenance.EvidenceKind` vocabulary. ``locator`` is
    optional inert location text and is never resolved, opened, or fetched.
    ``required_fields`` names the data fields an observation is expected to
    contain; naming a field neither produces it nor asserts it exists.

    The spec cannot say that an observation happened, that evidence matched, or
    that an action succeeded: no observed/actual/result/success field exists.
    """

    expectation: str
    evidence_kind: EvidenceKind
    locator: str | None = None
    required_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expectation",
            _validate_text(
                self.expectation, field_name="observe.expectation", max_length=_MAX_LONG_TEXT_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "evidence_kind",
            _validate_evidence_kind(self.evidence_kind, field_name="observe.evidence_kind"),
        )
        object.__setattr__(
            self,
            "locator",
            _validate_optional_text(
                self.locator, field_name="observe.locator", max_length=_MAX_SHORT_TEXT_LENGTH
            ),
        )
        object.__setattr__(self, "required_fields", _validate_field_names(self.required_fields))

    def to_params(self) -> dict[str, object]:
        """Return the canonical JSON-compatible ``ProcedureNode.params`` payload."""
        return {
            "expectation": self.expectation,
            "evidence_kind": self.evidence_kind.value,
            "locator": self.locator,
            "required_fields": list(self.required_fields),
        }

    @classmethod
    def from_params(cls, raw: Mapping[str, object]) -> ObserveNodeSpec:
        """Parse OBSERVE semantics out of an opaque node ``params`` mapping."""
        copied = _params_mapping(raw, field_name="observe params")
        _validate_exact_fields(copied, expected=OBSERVE_PARAM_FIELDS, field_name="observe params")
        return cls(
            expectation=_validate_text(
                copied["expectation"],
                field_name="observe.expectation",
                max_length=_MAX_LONG_TEXT_LENGTH,
            ),
            evidence_kind=_validate_evidence_kind(
                copied["evidence_kind"], field_name="observe.evidence_kind"
            ),
            locator=_validate_optional_text(
                copied["locator"],
                field_name="observe.locator",
                max_length=_MAX_SHORT_TEXT_LENGTH,
            ),
            required_fields=_validate_field_names(copied["required_fields"]),
        )

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build the corresponding A3.01 OBSERVE node (structure unchanged)."""
        return ProcedureNode(
            id=_node_id(node_id),
            kind=ProcedureNodeKind.OBSERVE,
            label=label,
            params=self.to_params(),
        )

    @classmethod
    def from_node(cls, node: ProcedureNode) -> ObserveNodeSpec:
        """Read OBSERVE semantics from an A3.01 node, rejecting other kinds."""
        return cls.from_params(_require_kind(node, expected=ProcedureNodeKind.OBSERVE).params)


def _validate_field_names(value: object) -> tuple[str, ...]:
    """Validate an ordered, duplicate-free tuple of expected field names."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ProcedureNodeContractError("observe.required_fields must be a sequence of strings")
    names: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        name = _validate_text(
            item, field_name=f"observe.required_fields[{index}]", max_length=_MAX_NAME_LENGTH
        )
        if name in seen:
            raise ProcedureNodeContractError(
                f"observe.required_fields contains a duplicate name: {name!r}"
            )
        seen.add(name)
        names.append(name)
    return tuple(names)


# --------------------------------------------------------------------------
# VERIFY.
# --------------------------------------------------------------------------


class VerificationRequirementStrength(StrEnum):
    """How strictly a described verification requirement must be satisfied.

    This is a property of the *requirement*, not of any outcome. ``REQUIRED``
    means the requirement must be satisfied before the procedure may be
    considered complete by whatever later, separately-owned component decides
    that; ``ADVISORY`` records a recommended check. Neither value verifies
    anything, and neither grants authority to skip verification.
    """

    REQUIRED = "required"
    ADVISORY = "advisory"


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifyNodeSpec:
    """An explicit verification *requirement* encoded as data — never a verdict.

    ``requirement`` states what must be verified and ``criterion`` states the
    inert, human-readable condition that would satisfy it. ``evidence_kind``
    records which canonical evidence kind the requirement is stated against, and
    ``strength`` records whether the requirement is required or advisory.

    The spec deliberately has no ``passed``, ``verdict``, ``result``, or
    ``verified`` field: it is structurally impossible for a VERIFY node to
    assert that verification occurred or succeeded. Producing an actual verdict
    remains the job of the canonical
    :class:`agentx.capabilities.abi.VerificationResult`, determined elsewhere
    against real evidence — this module never performs, delegates, simulates, or
    short-circuits that check.
    """

    requirement: str
    criterion: str
    evidence_kind: EvidenceKind
    strength: VerificationRequirementStrength = VerificationRequirementStrength.REQUIRED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requirement",
            _validate_text(
                self.requirement, field_name="verify.requirement", max_length=_MAX_LONG_TEXT_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "criterion",
            _validate_text(
                self.criterion, field_name="verify.criterion", max_length=_MAX_LONG_TEXT_LENGTH
            ),
        )
        object.__setattr__(
            self,
            "evidence_kind",
            _validate_evidence_kind(self.evidence_kind, field_name="verify.evidence_kind"),
        )
        object.__setattr__(self, "strength", _validate_strength(self.strength))

    def to_params(self) -> dict[str, object]:
        """Return the canonical JSON-compatible ``ProcedureNode.params`` payload."""
        return {
            "requirement": self.requirement,
            "criterion": self.criterion,
            "evidence_kind": self.evidence_kind.value,
            "strength": self.strength.value,
        }

    @classmethod
    def from_params(cls, raw: Mapping[str, object]) -> VerifyNodeSpec:
        """Parse VERIFY semantics out of an opaque node ``params`` mapping."""
        copied = _params_mapping(raw, field_name="verify params")
        _validate_exact_fields(copied, expected=VERIFY_PARAM_FIELDS, field_name="verify params")
        return cls(
            requirement=_validate_text(
                copied["requirement"],
                field_name="verify.requirement",
                max_length=_MAX_LONG_TEXT_LENGTH,
            ),
            criterion=_validate_text(
                copied["criterion"],
                field_name="verify.criterion",
                max_length=_MAX_LONG_TEXT_LENGTH,
            ),
            evidence_kind=_validate_evidence_kind(
                copied["evidence_kind"], field_name="verify.evidence_kind"
            ),
            strength=_validate_strength(copied["strength"]),
        )

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build the corresponding A3.01 VERIFY node (structure unchanged)."""
        return ProcedureNode(
            id=_node_id(node_id),
            kind=ProcedureNodeKind.VERIFY,
            label=label,
            params=self.to_params(),
        )

    @classmethod
    def from_node(cls, node: ProcedureNode) -> VerifyNodeSpec:
        """Read VERIFY semantics from an A3.01 node, rejecting other kinds."""
        return cls.from_params(_require_kind(node, expected=ProcedureNodeKind.VERIFY).params)


def _validate_strength(value: object) -> VerificationRequirementStrength:
    if isinstance(value, VerificationRequirementStrength):
        return value
    if not isinstance(value, str):
        raise ProcedureNodeContractError("verify.strength must be a strength string")
    try:
        return VerificationRequirementStrength(value)
    except ValueError as exc:
        raise ProcedureNodeContractError(
            f"unknown verification requirement strength: {value!r}"
        ) from exc
