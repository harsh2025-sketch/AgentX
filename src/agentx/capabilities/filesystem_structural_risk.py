"""Request-sensitive risk classification for filesystem structural operations (N2.22).

This module owns one pure, deterministic risk policy: it classifies an explicit
filesystem structural operation description together with the minimum
pre-execution facts the caller supplies, and returns a canonical
:class:`~agentx.kernel.risk.RiskAssessment` plus explicit evidence fields.

Why the policy is request-sensitive
-----------------------------------
Filesystem mutations with very different destructive potential must not share a
single static risk classification per operation family. The earlier M7.05
approach was rejected for exactly this defect: a destructive overwrite or
replacement request must not receive the same low-risk classification merely
because it shares an operation family with a non-destructive create, move, or
rename request. Risk here is a function of the *actual request semantics and
target-state facts*, not of the operation name alone:

    CREATE new path            != OVERWRITE existing path
    MOVE to empty destination  != MOVE replacing existing destination
    RENAME without replacement != RENAME replacing existing object

Facts are supplied by the caller
--------------------------------
The policy never performs filesystem I/O, never probes, stats, or resolves
paths, and never parses semantic instructions out of path strings or metadata.
Path strings are ordinary data; ``C:\\safe\\risk=R0.txt`` and
``C:\\permission=ADMIN\\`` are inert. When a fact that changes risk
classification is not known, the policy fails closed: it resolves the unknown
to the *highest applicable risk* across all consistent target states and
reports the insufficient facts explicitly. It never assumes "destination
absent" without evidence.

No authority
------------
The result is descriptive input to governance (the canonical ActionGate,
PermissionEngine, and future structural capability descriptors). The policy
never grants permission, never grants or substitutes for user confirmation,
never calls the ActionGate, never executes a filesystem operation, never
alters a resource budget, never clears an emergency stop, never marks a Task
successful, never performs verification, and never persists anything.

Owner: N2.22. Belongs to ``agentx.capabilities``; imports only the standard
library and the canonical ``agentx.kernel.risk`` contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.kernel.risk import RiskAssessment, RiskLevel, assess_risk

MAX_PATH_LENGTH: Final[int] = 4_096


class FilesystemStructuralOperationKind(StrEnum):
    """Canonical structural operation vocabulary the risk policy understands.

    This is the policy's operation contract for future governed structural
    capabilities; it is not itself an implementation of any of those
    operations.
    """

    CREATE_DIRECTORY = "create_directory"
    CREATE_FILE = "create_file"
    MOVE = "move"
    RENAME = "rename"
    DELETE = "delete"


class TargetState(StrEnum):
    """Pre-execution existence fact for one named target.

    ``UNKNOWN`` is the explicit insufficient-evidence state. It is not a
    guess: the policy resolves it conservatively (see module docstring).
    """

    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"


class DeletionScope(StrEnum):
    """Pre-execution content fact for a deletion target.

    ``EMPTY`` asserts the verified target is an empty directory or empty file
    (nothing destroyable). ``UNKNOWN`` means content evidence is unavailable
    and is resolved conservatively as potentially containing user data.
    """

    EMPTY = "empty"
    NON_EMPTY = "non_empty"
    UNKNOWN = "unknown"


def _validated_path(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must be trimmed")
    if len(value) > MAX_PATH_LENGTH:
        raise ValueError(f"{name} exceeds the governed maximum length of {MAX_PATH_LENGTH}")
    return value


@dataclass(frozen=True, slots=True)
class FilesystemStructuralRiskRequest:
    """One explicit structural operation description plus classification facts.

    Fields not applicable to an operation must stay at their neutral value:
    ``source_path``/``source_state`` are move/rename-only, ``deletion_scope``
    is delete-only, ``overwrite_requested`` is create/move-only, and
    ``replacement_requested`` is rename-only. Structural type validation
    raises :class:`TypeError`; semantic or contradictory facts raise
    :class:`ValueError`. The request is immutable and is never mutated by the
    policy.
    """

    operation: FilesystemStructuralOperationKind
    source_path: str | None
    destination_path: str
    source_state: TargetState = TargetState.UNKNOWN
    destination_state: TargetState = TargetState.UNKNOWN
    overwrite_requested: bool = False
    replacement_requested: bool = False
    deletion_scope: DeletionScope = DeletionScope.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.operation, FilesystemStructuralOperationKind):
            raise TypeError(
                "operation must be a FilesystemStructuralOperationKind, got "
                f"{type(self.operation).__name__}"
            )
        if not isinstance(self.source_state, TargetState):
            raise TypeError(
                f"source_state must be a TargetState, got {type(self.source_state).__name__}"
            )
        if not isinstance(self.destination_state, TargetState):
            raise TypeError(
                "destination_state must be a TargetState, got "
                f"{type(self.destination_state).__name__}"
            )
        if not isinstance(self.deletion_scope, DeletionScope):
            raise TypeError(
                f"deletion_scope must be a DeletionScope, got {type(self.deletion_scope).__name__}"
            )
        if type(self.overwrite_requested) is not bool:
            raise TypeError(
                f"overwrite_requested must be a bool, got {type(self.overwrite_requested).__name__}"
            )
        if type(self.replacement_requested) is not bool:
            raise TypeError(
                "replacement_requested must be a bool, got "
                f"{type(self.replacement_requested).__name__}"
            )

        object.__setattr__(
            self, "destination_path", _validated_path(self.destination_path, "destination_path")
        )

        needs_source = self.operation in (
            FilesystemStructuralOperationKind.MOVE,
            FilesystemStructuralOperationKind.RENAME,
        )
        if needs_source:
            if self.source_path is None:
                raise ValueError("source_path is required for move and rename operations")
            object.__setattr__(
                self, "source_path", _validated_path(self.source_path, "source_path")
            )
        elif self.source_path is not None:
            raise ValueError(f"source_path is not applicable to {self.operation.value} operations")
        if not needs_source and self.source_state is not TargetState.UNKNOWN:
            raise ValueError(
                "source_state is only applicable to move and rename operations; "
                "unknown is the required neutral value"
            )

        if self.replacement_requested and self.operation is not (
            FilesystemStructuralOperationKind.RENAME
        ):
            raise ValueError(
                "replacement_requested is only applicable to rename operations; "
                "use overwrite_requested for create and move"
            )
        if self.overwrite_requested and self.operation not in (
            FilesystemStructuralOperationKind.CREATE_DIRECTORY,
            FilesystemStructuralOperationKind.CREATE_FILE,
            FilesystemStructuralOperationKind.MOVE,
        ):
            raise ValueError(
                "overwrite_requested is only applicable to create and move operations; "
                "use replacement_requested for rename"
            )
        if self.deletion_scope is not DeletionScope.UNKNOWN and self.operation is not (
            FilesystemStructuralOperationKind.DELETE
        ):
            raise ValueError("deletion_scope is only applicable to delete operations")
        if (
            self.operation is FilesystemStructuralOperationKind.DELETE
            and self.destination_state is TargetState.ABSENT
            and self.deletion_scope is not DeletionScope.UNKNOWN
        ):
            raise ValueError(
                "contradictory facts: a verified-absent deletion target cannot carry "
                "an empty/non-empty content claim"
            )


@dataclass(frozen=True, slots=True)
class _WorldFacts:
    """Fully determined classification of one consistent target-state world."""

    read_only: bool
    modifies_state: bool
    reversible: bool
    external_effect: bool
    critical: bool
    destructive: bool
    data_loss_possible: bool
    note: str

    @property
    def level(self) -> RiskLevel:
        return assess_risk(
            read_only=self.read_only,
            modifies_state=self.modifies_state,
            reversible=self.reversible,
            external_effect=self.external_effect,
            critical=self.critical,
            destructive=self.destructive,
        ).level


@dataclass(frozen=True, slots=True)
class FilesystemStructuralRiskResult:
    """Immutable, request-sensitive classification outcome.

    ``assessment`` is the canonical R0-R4 classification (descriptive input to
    governance, never authorization). ``state_change_possible`` and
    ``data_loss_possible`` are evidence fields derived from the same facts.
    ``insufficient_facts`` names the risk-relevant facts that lacked
    pre-execution evidence and were resolved to the higher applicable risk.
    """

    assessment: RiskAssessment
    state_change_possible: bool
    data_loss_possible: bool
    insufficient_facts: tuple[str, ...]


def _no_op(note: str) -> _WorldFacts:
    return _WorldFacts(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=False,
        critical=False,
        destructive=False,
        data_loss_possible=False,
        note=note,
    )


def _reversible_creation(label: str) -> _WorldFacts:
    return _WorldFacts(
        read_only=False,
        modifies_state=True,
        reversible=True,
        external_effect=False,
        critical=False,
        destructive=False,
        data_loss_possible=False,
        note=(
            f"creates a new empty {label} where none exists; the change is "
            "reversible and no user data can be lost"
        ),
    )


def _destructive_replacement(label: str) -> _WorldFacts:
    return _WorldFacts(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=False,
        critical=False,
        destructive=True,
        data_loss_possible=True,
        note=(
            f"replaces an existing {label}; the prior target is destroyed and "
            "user data loss is possible"
        ),
    )


def _move_to_absent_destination() -> _WorldFacts:
    return _WorldFacts(
        read_only=False,
        modifies_state=True,
        reversible=True,
        external_effect=False,
        critical=False,
        destructive=False,
        data_loss_possible=False,
        note=(
            "moves the source to a verified-absent destination; the source "
            "content is preserved exactly and the change is reversible"
        ),
    )


def _empty_structure_removal() -> _WorldFacts:
    return _WorldFacts(
        read_only=False,
        modifies_state=True,
        reversible=True,
        external_effect=False,
        critical=False,
        destructive=False,
        data_loss_possible=False,
        note=(
            "removes a verified-empty structure; no user data is destroyed "
            "and the change is reversible"
        ),
    )


def _destructive_removal() -> _WorldFacts:
    return _WorldFacts(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=False,
        critical=False,
        destructive=True,
        data_loss_possible=True,
        note=(
            "removes a target that contains user data, or whose content is "
            "unverified and therefore treated as containing data; user data "
            "loss is possible"
        ),
    )


def _classify_world(
    request: FilesystemStructuralRiskRequest,
    source_state: TargetState,
    destination_state: TargetState,
    deletion_scope: DeletionScope,
) -> _WorldFacts:
    """Classify one fully resolved (no ``UNKNOWN`` facts) target-state world."""
    operation = request.operation

    if operation in (
        FilesystemStructuralOperationKind.CREATE_DIRECTORY,
        FilesystemStructuralOperationKind.CREATE_FILE,
    ):
        label = (
            "directory"
            if operation is FilesystemStructuralOperationKind.CREATE_DIRECTORY
            else "file"
        )
        if destination_state is TargetState.ABSENT:
            return _reversible_creation(label)
        if request.overwrite_requested:
            return _destructive_replacement(label)
        return _no_op(
            "the target path is verified present and create-only semantics "
            "prohibit replacement, so the operation fails before any mutation"
        )

    if operation is FilesystemStructuralOperationKind.MOVE:
        if source_state is TargetState.ABSENT:
            return _no_op(
                "the source is verified absent, so there is nothing to move "
                "and no mutation can occur"
            )
        if destination_state is TargetState.ABSENT:
            return _move_to_absent_destination()
        if request.overwrite_requested:
            return _destructive_replacement("destination file or directory")
        return _no_op(
            "the destination is verified present and overwrite was not "
            "requested, so the operation fails before any mutation"
        )

    if operation is FilesystemStructuralOperationKind.RENAME:
        if source_state is TargetState.ABSENT:
            return _no_op(
                "the source is verified absent, so there is nothing to rename "
                "and no mutation can occur"
            )
        if destination_state is TargetState.ABSENT:
            return _move_to_absent_destination()
        if request.replacement_requested:
            return _destructive_replacement("destination object")
        return _no_op(
            "the destination is verified present and replacement was not "
            "requested, so the operation fails before any mutation"
        )

    # DELETE
    if destination_state is TargetState.ABSENT:
        return _no_op(
            "the target is verified absent, so there is nothing to remove and no mutation can occur"
        )
    if deletion_scope is DeletionScope.EMPTY:
        return _empty_structure_removal()
    return _destructive_removal()


def _resolve_source_states(
    request: FilesystemStructuralRiskRequest,
    overrides: Mapping[str, TargetState],
) -> tuple[TargetState, ...]:
    if "source_state" in overrides:
        return (overrides["source_state"],)
    if request.source_state is TargetState.UNKNOWN:
        return (TargetState.ABSENT, TargetState.PRESENT)
    return (request.source_state,)


def _resolve_destination_states(
    request: FilesystemStructuralRiskRequest,
    overrides: Mapping[str, TargetState],
) -> tuple[TargetState, ...]:
    if "destination_state" in overrides:
        return (overrides["destination_state"],)
    if request.destination_state is TargetState.UNKNOWN:
        return (TargetState.ABSENT, TargetState.PRESENT)
    return (request.destination_state,)


def _resolve_scopes(
    request: FilesystemStructuralRiskRequest,
    overrides: Mapping[str, DeletionScope],
) -> tuple[DeletionScope, ...]:
    if "deletion_scope" in overrides:
        return (overrides["deletion_scope"],)
    if request.deletion_scope is DeletionScope.UNKNOWN:
        # Conservative enumeration: unknown content is resolved across both
        # possible content states; the join keeps the worst world.
        return (DeletionScope.EMPTY, DeletionScope.NON_EMPTY)
    return (request.deletion_scope,)


def _worlds_for(
    request: FilesystemStructuralRiskRequest,
    state_overrides: Mapping[str, TargetState],
    scope_overrides: Mapping[str, DeletionScope],
) -> tuple[_WorldFacts, ...]:
    return tuple(
        _classify_world(request, source, destination, scope)
        for source in _resolve_source_states(request, state_overrides)
        for destination in _resolve_destination_states(request, state_overrides)
        for scope in _resolve_scopes(request, scope_overrides)
    )


@dataclass(frozen=True, slots=True)
class _JoinedClassification:
    """Conservative join of all consistent target-state worlds."""

    level: RiskLevel
    read_only: bool
    modifies_state: bool
    reversible: bool
    external_effect: bool
    critical: bool
    destructive: bool
    data_loss_possible: bool
    note: str


def _join_worlds(worlds: tuple[_WorldFacts, ...]) -> _JoinedClassification:
    read_only = all(world.read_only for world in worlds)
    modifies_state = any(world.modifies_state for world in worlds)
    modifying = tuple(world for world in worlds if world.modifies_state)
    reversible = bool(modifying) and all(world.reversible for world in modifying)
    external_effect = any(world.external_effect for world in worlds)
    critical = any(world.critical for world in worlds)
    destructive = any(world.destructive for world in worlds)
    data_loss_possible = any(world.data_loss_possible for world in worlds)

    # The level is derived from the joined characteristics through the
    # canonical risk model, so the stored level can never undercut the
    # characteristics' floor. The deterministic worst world (maximum level,
    # then data loss, first in enumeration order) supplies the explanation.
    worst = max(worlds, key=lambda world: (world.level.severity, world.data_loss_possible))
    level = assess_risk(
        read_only=read_only,
        modifies_state=modifies_state,
        reversible=reversible,
        external_effect=external_effect,
        critical=critical,
        destructive=destructive,
    ).level

    return _JoinedClassification(
        level=level,
        read_only=read_only,
        modifies_state=modifies_state,
        reversible=reversible,
        external_effect=external_effect,
        critical=critical,
        destructive=destructive,
        data_loss_possible=data_loss_possible,
        note=worst.note,
    )


def _summary_for(
    request: FilesystemStructuralRiskRequest,
    *,
    state_fact: str | None = None,
    state_value: TargetState | None = None,
    scope_value: DeletionScope | None = None,
) -> tuple[RiskLevel, bool]:
    """Return ``(level, data_loss)`` with one fact pinned to a concrete value."""
    state_overrides: dict[str, TargetState] = {}
    if state_fact is not None:
        if state_value is None:
            raise ValueError("state_value is required with state_fact")
        state_overrides[state_fact] = state_value
    scope_overrides: dict[str, DeletionScope] = {}
    if scope_value is not None:
        scope_overrides["deletion_scope"] = scope_value
    joined = _join_worlds(_worlds_for(request, state_overrides, scope_overrides))
    return joined.level, joined.data_loss_possible


def _insufficient_facts(request: FilesystemStructuralRiskRequest) -> tuple[str, ...]:
    """Name the risk-relevant facts lacking pre-execution evidence.

    A fact is reported only when it was supplied as ``UNKNOWN`` *and* its
    different possible values change the classification (level or data-loss
    exposure). Unknown facts that do not change the outcome (for example an
    unknown destination on a request guaranteed to be a no-op) are not
    reported.
    """
    relevant: list[str] = []
    for fact in ("source_state", "destination_state"):
        supplied = request.source_state if fact == "source_state" else request.destination_state
        if supplied is not TargetState.UNKNOWN:
            continue
        absent = _summary_for(request, state_fact=fact, state_value=TargetState.ABSENT)
        present = _summary_for(request, state_fact=fact, state_value=TargetState.PRESENT)
        if absent != present:
            relevant.append(fact)
    if (
        request.operation is FilesystemStructuralOperationKind.DELETE
        and request.deletion_scope is DeletionScope.UNKNOWN
    ):
        empty = _summary_for(request, scope_value=DeletionScope.EMPTY)
        non_empty = _summary_for(request, scope_value=DeletionScope.NON_EMPTY)
        if empty != non_empty:
            relevant.append("deletion_scope")
    return tuple(sorted(relevant))


def _compose_reason(level: RiskLevel, note: str, insufficient: tuple[str, ...]) -> str:
    reason = f"{level.label}: {note}."
    if insufficient:
        facts = ", ".join(insufficient)
        reason += (
            f" Insufficient pre-execution evidence for {facts} forced the "
            "higher applicable risk; the missing state was not assumed absent."
        )
    return reason


def classify_filesystem_structural_risk(
    request: FilesystemStructuralRiskRequest,
) -> FilesystemStructuralRiskResult:
    """Classify one explicit filesystem structural operation description.

    Pure and deterministic: the result depends only on the request's typed
    facts. No filesystem I/O, no path probing, no authority decision, no
    Task transition, no verification, no budget, and no persistence occur.
    Unknown risk-relevant facts are resolved to the highest applicable risk
    and reported in ``insufficient_facts``.
    """
    if not isinstance(request, FilesystemStructuralRiskRequest):
        raise TypeError(
            f"request must be a FilesystemStructuralRiskRequest, got {type(request).__name__}"
        )

    joined = _join_worlds(_worlds_for(request, {}, {}))
    insufficient = _insufficient_facts(request)
    reason = _compose_reason(joined.level, joined.note, insufficient)

    assessment = RiskAssessment(
        level=joined.level,
        reason=reason,
        read_only=joined.read_only,
        modifies_state=joined.modifies_state,
        reversible=joined.reversible,
        external_effect=joined.external_effect,
        critical=joined.critical,
        destructive=joined.destructive,
    )
    return FilesystemStructuralRiskResult(
        assessment=assessment,
        state_change_possible=joined.modifies_state,
        data_loss_possible=joined.data_loss_possible,
        insufficient_facts=insufficient,
    )


class FilesystemStructuralRiskPolicy:
    """Stateless request-sensitive risk policy for filesystem structural operations.

    The object form mirrors the canonical kernel components (``ActionGate``,
    ``PermissionEngine``): the policy holds no state, no collaborators, and no
    authority. Its single method delegates to the pure
    :func:`classify_filesystem_structural_risk` function.
    """

    __slots__ = ()

    def classify(self, request: FilesystemStructuralRiskRequest) -> FilesystemStructuralRiskResult:
        """Classify one explicit structural operation description and its facts."""
        return classify_filesystem_structural_risk(request)


__all__ = [
    "MAX_PATH_LENGTH",
    "DeletionScope",
    "FilesystemStructuralOperationKind",
    "FilesystemStructuralRiskPolicy",
    "FilesystemStructuralRiskRequest",
    "FilesystemStructuralRiskResult",
    "TargetState",
    "classify_filesystem_structural_risk",
]
