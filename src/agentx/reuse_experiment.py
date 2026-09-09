"""Deterministic cold-vs-warm verified-reuse experiment harness (N2.12).

This module is the **measurement/orchestration boundary** for the canonical
AgentX reuse experiment. It drives two bounded phases around *injected
execution ports* and records what actually happened:

* Phase A — **COLD**: the measured task is completed by an execution path that
  does **not** claim reusable prior execution. Cold evidence modes are the
  canonical ``ReuseMode`` values that make no reuse claim:
  ``DETERMINISTIC_CAPABILITY`` (an L1 direct deterministic path), ``NOVEL_PLAN``
  (L4 planned execution), and ``EXPLORATORY`` (L5 exploratory execution).
* Phase B — **WARM**: the measured task (or an explicitly related task) is
  completed by an execution path that claims explicit reuse evidence according
  to the canonical M8.01 reuse-mode vocabulary: ``CACHE_REUSE`` (an L0 cached
  verified result), ``PROCEDURE_REUSE`` (an L2 compiled procedure), or
  ``GUIDED_PROCEDURE`` (an L3 procedure with reasoning gaps).

Cold/warm is classified **only** from the typed ``ReuseMode`` of the evidence
each injected runner returns. It is never inferred from execution time,
procedure name text, cost, model-call counts, or any other measured value, and
the harness never decides *which* execution path or Procedure a phase should
use: it measures what the caller actually executed.

All measured facts, metric arithmetic, ratio/delta rules, zero-denominator
handling, verified-success truth boundaries, procedure revision binding, task
comparability, and the conservative disposition policy come verbatim from the
landed M8.01 contracts in :mod:`agentx.core.reuse_efficiency`. This module adds
no metric of its own and re-implements none of that logic.

Verified success stays intact by construction: a cheaper or faster warm run
that is not a canonical verified success can never produce an improvement
verdict, because M8.01 itself returns ``REGRESSED``/``INSUFFICIENT_EVIDENCE``
for such runs and this harness only maps canonical dispositions.

Determinism
-----------

The harness reads no clock, generates no randomness, performs no network or
model call, and performs no persistence. All measurements arrive as explicit
canonical evidence from the injected runners, so unit tests use deterministic
fakes. Each phase is executed by exactly one port call — there are no retries,
loops, sleeps, or wall-clock measurements here. A runner may persist its own
results externally; that is the runner's business and is invisible here.

Experiment results are evidence only
------------------------------------

A ``ReuseExperimentResult`` — including one whose verdict says the warm phase
improved — is inert data. It cannot route future tasks, select or activate a
Procedure, promote a candidate, grant Permission, lower Risk, widen a
ResourceEnvelope, bypass the Action Gate, clear an EmergencyStop, transition a
Task, or mark any Task successful. No policy is auto-updated from a result.
``agentx.core.reuse_efficiency`` remains the canonical measurement truth
boundary; this module is only the experiment harness around it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ExecutionEfficiencyEvidence,
    MetricComparison,
    ProcedureRevisionRef,
    ReuseComparison,
    ReuseEfficiencyDisposition,
    ReuseEfficiencyValidationError,
    ReuseMode,
    TaskRelationshipEvidence,
    compare_execution_efficiency,
)

REUSE_EXPERIMENT_SCHEMA_VERSION: Final[int] = 1
_MAX_TEXT_LENGTH: Final[int] = 1_024


class ReuseExperimentError(ValueError):
    """Raised when the reuse-experiment harness contract is violated."""


class ReuseExperimentDeserializationError(ReuseExperimentError):
    """Raised when encoded experiment evidence cannot be reconstructed safely."""


class UnsupportedReuseExperimentSchemaVersionError(ReuseExperimentDeserializationError):
    """Raised when encoded experiment evidence uses an unsupported schema version."""


class PhaseEvidenceError(ReuseExperimentError):
    """Raised when an injected phase runner returns non-canonical evidence.

    The harness never parses untyped data (dicts, strings, ``verified=true``
    text) into measurements: a phase port must return a canonical
    :class:`~agentx.core.reuse_efficiency.ExecutionEfficiencyEvidence`.
    """


class ExperimentPhase(StrEnum):
    """The two bounded phases of one cold-vs-warm experiment."""

    COLD = "cold"
    WARM = "warm"


class ReuseExperimentVerdict(StrEnum):
    """Deterministic experiment verdict; a strict mapping of M8.01 dispositions.

    ``WARM_*`` values are *measured evidence* about the two recorded phases.
    They carry no authority and must never feed routing, Procedure activation,
    permission, risk, budget, or Task-state policy.
    """

    WARM_IMPROVED = "warm_improved"
    WARM_NO_MATERIAL_IMPROVEMENT = "warm_no_material_improvement"
    WARM_REGRESSED = "warm_regressed"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID_EXPERIMENT = "invalid_experiment"


#: Canonical reuse modes that claim reusable prior execution (warm phases).
WARM_REUSE_MODES: Final[frozenset[ReuseMode]] = frozenset(
    {
        ReuseMode.CACHE_REUSE,
        ReuseMode.PROCEDURE_REUSE,
        ReuseMode.GUIDED_PROCEDURE,
    }
)

#: Canonical reuse modes that make no reuse claim (cold phases).
COLD_NO_REUSE_MODES: Final[frozenset[ReuseMode]] = frozenset(
    {
        ReuseMode.DETERMINISTIC_CAPABILITY,
        ReuseMode.NOVEL_PLAN,
        ReuseMode.EXPLORATORY,
    }
)

_VERDICT_BY_DISPOSITION: Final[Mapping[ReuseEfficiencyDisposition, ReuseExperimentVerdict]] = {
    ReuseEfficiencyDisposition.IMPROVED: ReuseExperimentVerdict.WARM_IMPROVED,
    ReuseEfficiencyDisposition.NO_MATERIAL_IMPROVEMENT: (
        ReuseExperimentVerdict.WARM_NO_MATERIAL_IMPROVEMENT
    ),
    ReuseEfficiencyDisposition.REGRESSED: ReuseExperimentVerdict.WARM_REGRESSED,
    ReuseEfficiencyDisposition.NOT_COMPARABLE: ReuseExperimentVerdict.NOT_COMPARABLE,
    ReuseEfficiencyDisposition.INSUFFICIENT_EVIDENCE: ReuseExperimentVerdict.INSUFFICIENT_EVIDENCE,
}


def classify_reuse_mode(mode: ReuseMode) -> ExperimentPhase:
    """Classify one canonical reuse mode as a cold or warm execution phase.

    The closed mapping is total over the M8.01 ``ReuseMode`` vocabulary:
    reuse-claiming modes (``CACHE_REUSE``, ``PROCEDURE_REUSE``,
    ``GUIDED_PROCEDURE``) are warm; non-claiming modes
    (``DETERMINISTIC_CAPABILITY``, ``NOVEL_PLAN``, ``EXPLORATORY``) are cold.
    This is a data classification only — it does not route, select, or execute
    anything, and it never inspects text.
    """

    if not isinstance(mode, ReuseMode):
        raise TypeError("mode must be a ReuseMode")
    if mode in WARM_REUSE_MODES:
        return ExperimentPhase.WARM
    return ExperimentPhase.COLD


# ---------------------------------------------------------------------------
# Local validation helpers (canonical contracts stay private to their module).
# ---------------------------------------------------------------------------


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ReuseExperimentError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ReuseExperimentError(f"{field_name} must not exceed {_MAX_TEXT_LENGTH} characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ReuseExperimentError(f"{field_name} must not contain control lines")
    return value


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ReuseExperimentError(f"{field_name} must not be the nil UUID")
    return value


def _validate_schema_version(value: object) -> int:
    if type(value) is not int:
        raise TypeError("schema_version must be an integer")
    if value != REUSE_EXPERIMENT_SCHEMA_VERSION:
        raise UnsupportedReuseExperimentSchemaVersionError(
            f"unsupported reuse-experiment schema version {value}; "
            f"supported version is {REUSE_EXPERIMENT_SCHEMA_VERSION}"
        )
    return value


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise ReuseExperimentDeserializationError(f"{field_name} must be a string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ReuseExperimentDeserializationError(f"{field_name} is not a valid UUID") from exc
    if parsed.int == 0:
        raise ReuseExperimentDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_task_id(value: object, *, field_name: str) -> TaskId:
    if not isinstance(value, str):
        raise ReuseExperimentDeserializationError(f"{field_name} must be a string")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise ReuseExperimentDeserializationError(f"{field_name} is not a valid TaskId") from exc


def _parse_enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, *, field_name: str) -> EnumT:
    if not isinstance(value, str):
        raise ReuseExperimentDeserializationError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ReuseExperimentDeserializationError(
            f"{field_name} is not a supported {enum_type.__name__}"
        ) from exc


def _require_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ReuseExperimentDeserializationError(
            f"{context} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise ReuseExperimentDeserializationError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


# ---------------------------------------------------------------------------
# Experiment specification and injected execution ports.
# ---------------------------------------------------------------------------


_SPEC_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "experiment_id",
        "cold_task_id",
        "warm_task_id",
        "relationship",
        "expected_warm_procedure",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReuseExperimentSpec:
    """Immutable declaration of what one experiment intends to measure.

    The spec is *intent*, not selection: it names the task each phase measures
    and optionally the exact ``ProcedureId``/revision the warm phase is expected
    to reuse plus the canonical cross-task relationship evidence. The harness
    never chooses a Procedure or a route; it only verifies that the measured
    evidence matches what the caller declared.
    """

    experiment_id: UUID
    cold_task_id: TaskId
    warm_task_id: TaskId
    relationship: TaskRelationshipEvidence | None = None
    expected_warm_procedure: ProcedureRevisionRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_id", _validate_uuid(self.experiment_id, field_name="experiment_id")
        )
        if not isinstance(self.cold_task_id, TaskId):
            raise TypeError("cold_task_id must be a TaskId")
        if not isinstance(self.warm_task_id, TaskId):
            raise TypeError("warm_task_id must be a TaskId")
        if self.relationship is not None and not isinstance(
            self.relationship, TaskRelationshipEvidence
        ):
            raise TypeError("relationship must be TaskRelationshipEvidence or None")
        if self.expected_warm_procedure is not None and not isinstance(
            self.expected_warm_procedure, ProcedureRevisionRef
        ):
            raise TypeError("expected_warm_procedure must be ProcedureRevisionRef or None")

    def to_dict(self) -> dict[str, object]:
        """Return the exact deterministic JSON-compatible schema."""

        return {
            "experiment_id": str(self.experiment_id),
            "cold_task_id": self.cold_task_id.to_str(),
            "warm_task_id": self.warm_task_id.to_str(),
            "relationship": None if self.relationship is None else self.relationship.to_dict(),
            "expected_warm_procedure": (
                None
                if self.expected_warm_procedure is None
                else self.expected_warm_procedure.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ReuseExperimentSpec:
        """Reconstruct a spec and reject missing/unknown fields."""

        _require_exact_fields(raw, expected=_SPEC_FIELDS, context="experiment spec")
        relationship_raw = raw["relationship"]
        if relationship_raw is not None and not isinstance(relationship_raw, Mapping):
            raise ReuseExperimentDeserializationError("relationship must be an object or null")
        procedure_raw = raw["expected_warm_procedure"]
        if procedure_raw is not None and not isinstance(procedure_raw, Mapping):
            raise ReuseExperimentDeserializationError(
                "expected_warm_procedure must be an object or null"
            )
        return cls(
            experiment_id=_parse_uuid(raw["experiment_id"], field_name="experiment_id"),
            cold_task_id=_parse_task_id(raw["cold_task_id"], field_name="cold_task_id"),
            warm_task_id=_parse_task_id(raw["warm_task_id"], field_name="warm_task_id"),
            relationship=(
                None
                if relationship_raw is None
                else TaskRelationshipEvidence.from_dict(relationship_raw)
            ),
            expected_warm_procedure=(
                None if procedure_raw is None else ProcedureRevisionRef.from_dict(procedure_raw)
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PhaseExecutionRequest:
    """Explicit bounded request for exactly one measured phase execution.

    The harness issues exactly one request per phase and never retries. The
    request carries only typed identity facts; it carries no authority, no
    budget, and no routing decision.
    """

    experiment_id: UUID
    phase: ExperimentPhase
    task_id: TaskId

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_id", _validate_uuid(self.experiment_id, field_name="experiment_id")
        )
        if not isinstance(self.phase, ExperimentPhase):
            raise TypeError("phase must be an ExperimentPhase")
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")


@runtime_checkable
class PhaseRunner(Protocol):
    """Injected execution port for one measured experiment phase.

    Implementations execute the requested phase once and return canonical
    :class:`~agentx.core.reuse_efficiency.ExecutionEfficiencyEvidence` carrying
    the measured facts (duration, model calls, tokens, cost, actions, procedure
    binding, typed verification, ...). The evidence is *supplied* by the runner:
    this protocol never requires the harness to observe a clock, call a model,
    or touch the network. A runner that cannot measure a dimension reports it
    as ``None``; a runner that fails reports a canonical failed outcome.
    """

    def run_phase(self, request: PhaseExecutionRequest) -> ExecutionEfficiencyEvidence: ...


# ---------------------------------------------------------------------------
# Deterministic experiment components.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ExperimentComponents:
    """Derived facts recomputed from typed evidence on every construction."""

    cold_classification: ExperimentPhase
    warm_classification: ExperimentPhase
    comparison: ReuseComparison | None
    verdict: ReuseExperimentVerdict
    reasons: tuple[str, ...]


def _procedure_binding_text(procedure: ProcedureRevisionRef) -> str:
    return f"procedure {procedure.procedure_id.to_str()} revision {procedure.revision}"


def _experiment_components(
    spec: ReuseExperimentSpec,
    cold: ExecutionEfficiencyEvidence,
    warm: ExecutionEfficiencyEvidence,
) -> _ExperimentComponents:
    """Derive every experiment verdict fact from typed evidence only.

    Harness-level validity checks run first (phase classification, declared
    task match, declared warm procedure binding); an invalid experiment keeps
    both evidence records but produces no comparison. Valid experiments defer
    entirely to the canonical M8.01 comparison, including its conservative
    disposition logic, and only translate its disposition one-to-one.
    """

    cold_classification = classify_reuse_mode(cold.mode)
    warm_classification = classify_reuse_mode(warm.mode)
    problems: list[str] = []

    if cold_classification is not ExperimentPhase.COLD:
        problems.append(
            f"cold phase evidence mode {cold.mode.value} claims reusable prior execution"
        )
    if warm_classification is not ExperimentPhase.WARM:
        problems.append(
            f"warm phase evidence mode {warm.mode.value} does not claim reusable prior execution"
        )
    if cold.task_id != spec.cold_task_id:
        problems.append(
            f"cold phase measured task {cold.task_id.to_str()} "
            f"but the spec declared task {spec.cold_task_id.to_str()}"
        )
    if warm.task_id != spec.warm_task_id:
        problems.append(
            f"warm phase measured task {warm.task_id.to_str()} "
            f"but the spec declared task {spec.warm_task_id.to_str()}"
        )
    expected = spec.expected_warm_procedure
    if expected is not None:
        if warm.procedure is None:
            problems.append(
                f"warm phase did not use the declared {_procedure_binding_text(expected)}"
            )
        elif warm.procedure != expected:
            problems.append(
                f"warm phase used {_procedure_binding_text(warm.procedure)} "
                f"but the spec declared {_procedure_binding_text(expected)}"
            )

    if problems:
        return _ExperimentComponents(
            cold_classification=cold_classification,
            warm_classification=warm_classification,
            comparison=None,
            verdict=ReuseExperimentVerdict.INVALID_EXPERIMENT,
            reasons=tuple(problems),
        )

    try:
        comparison = compare_execution_efficiency(cold, warm, relationship=spec.relationship)
    except ReuseEfficiencyValidationError as exc:
        return _ExperimentComponents(
            cold_classification=cold_classification,
            warm_classification=warm_classification,
            comparison=None,
            verdict=ReuseExperimentVerdict.INVALID_EXPERIMENT,
            reasons=(f"canonical comparison rejected the phase evidence: {exc}",),
        )

    reasons = (
        f"cold phase executed {cold.mode.value} without claiming reusable prior execution",
        f"warm phase executed {warm.mode.value} with explicit reuse evidence",
        *comparison.reasons,
    )
    return _ExperimentComponents(
        cold_classification=cold_classification,
        warm_classification=warm_classification,
        comparison=comparison,
        verdict=_VERDICT_BY_DISPOSITION[comparison.disposition],
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Harness and result.
# ---------------------------------------------------------------------------

_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "spec",
        "cold",
        "warm",
        "cold_classification",
        "warm_classification",
        "comparison",
        "verdict",
        "reasons",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReuseExperimentResult:
    """Immutable, inert evidence record for one completed experiment.

    Every derived field (phase classifications, canonical comparison, verdict,
    reasons) is recomputed from the typed evidence on construction and must
    match, so a result can never disagree with the evidence it carries. The
    record grants no authority: it must not be used to route tasks, activate
    Procedures, grant Permission, lower Risk, widen budgets, bypass the
    Action Gate, clear an EmergencyStop, or transition any Task.
    """

    spec: ReuseExperimentSpec
    cold: ExecutionEfficiencyEvidence
    warm: ExecutionEfficiencyEvidence
    cold_classification: ExperimentPhase
    warm_classification: ExperimentPhase
    comparison: ReuseComparison | None
    verdict: ReuseExperimentVerdict
    reasons: tuple[str, ...]
    schema_version: int = REUSE_EXPERIMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.spec, ReuseExperimentSpec):
            raise TypeError("spec must be a ReuseExperimentSpec")
        if not isinstance(self.cold, ExecutionEfficiencyEvidence):
            raise TypeError("cold must be ExecutionEfficiencyEvidence")
        if not isinstance(self.warm, ExecutionEfficiencyEvidence):
            raise TypeError("warm must be ExecutionEfficiencyEvidence")
        if not isinstance(self.cold_classification, ExperimentPhase):
            raise TypeError("cold_classification must be an ExperimentPhase")
        if not isinstance(self.warm_classification, ExperimentPhase):
            raise TypeError("warm_classification must be an ExperimentPhase")
        if self.comparison is not None and not isinstance(self.comparison, ReuseComparison):
            raise TypeError("comparison must be ReuseComparison or None")
        if not isinstance(self.verdict, ReuseExperimentVerdict):
            raise TypeError("verdict must be a ReuseExperimentVerdict")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise ReuseExperimentError("reasons must be a non-empty tuple")
        for reason in self.reasons:
            _validate_text(reason, field_name="experiment reason")
        object.__setattr__(self, "schema_version", _validate_schema_version(self.schema_version))

        expected = _experiment_components(self.spec, self.cold, self.warm)
        if self.cold_classification is not expected.cold_classification:
            raise ReuseExperimentError(
                "cold_classification does not match the cold phase evidence mode"
            )
        if self.warm_classification is not expected.warm_classification:
            raise ReuseExperimentError(
                "warm_classification does not match the warm phase evidence mode"
            )
        if self.comparison != expected.comparison:
            raise ReuseExperimentError(
                "comparison does not match the deterministic canonical comparison"
            )
        if self.verdict is not expected.verdict:
            raise ReuseExperimentError(
                "verdict does not match the deterministic experiment evidence"
            )
        if self.reasons != expected.reasons:
            raise ReuseExperimentError("reasons do not match the deterministic experiment")

    def metric_comparison(self, name: EfficiencyMetric) -> MetricComparison | None:
        """Return one canonical M8.01 metric comparison, or ``None``.

        Delegates verbatim to the canonical comparison; the harness defines no
        metric of its own. ``None`` is returned when the experiment is invalid
        (no comparison exists) or the dimension was not measured on both
        phases.
        """

        if not isinstance(name, EfficiencyMetric):
            raise TypeError("name must be an EfficiencyMetric")
        if self.comparison is None:
            return None
        return self.comparison.metric(name)

    def to_dict(self) -> dict[str, object]:
        """Return the exact deterministic JSON-compatible schema."""

        return {
            "schema_version": self.schema_version,
            "spec": self.spec.to_dict(),
            "cold": self.cold.to_dict(),
            "warm": self.warm.to_dict(),
            "cold_classification": self.cold_classification.value,
            "warm_classification": self.warm_classification.value,
            "comparison": None if self.comparison is None else self.comparison.to_dict(),
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
        }

    def to_json(self) -> str:
        """Serialize deterministically; no pickle or dynamic execution is used."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ReuseExperimentResult:
        """Reconstruct one result and reject tampered derived values."""

        _require_exact_fields(raw, expected=_RESULT_FIELDS, context="experiment result")
        _validate_schema_version(raw["schema_version"])
        spec_raw = raw["spec"]
        if not isinstance(spec_raw, Mapping):
            raise ReuseExperimentDeserializationError("spec must be an object")
        cold_raw = raw["cold"]
        warm_raw = raw["warm"]
        if not isinstance(cold_raw, Mapping) or not isinstance(warm_raw, Mapping):
            raise ReuseExperimentDeserializationError("cold and warm must be objects")
        comparison_raw = raw["comparison"]
        if comparison_raw is not None and not isinstance(comparison_raw, Mapping):
            raise ReuseExperimentDeserializationError("comparison must be an object or null")
        reasons_raw = raw["reasons"]
        if not isinstance(reasons_raw, list) or not reasons_raw:
            raise ReuseExperimentDeserializationError("reasons must be a non-empty array")
        if not all(isinstance(reason, str) for reason in reasons_raw):
            raise ReuseExperimentDeserializationError("reasons must be strings")
        return cls(
            spec=ReuseExperimentSpec.from_dict(spec_raw),
            cold=ExecutionEfficiencyEvidence.from_dict(cold_raw),
            warm=ExecutionEfficiencyEvidence.from_dict(warm_raw),
            cold_classification=_parse_enum(
                ExperimentPhase, raw["cold_classification"], field_name="cold_classification"
            ),
            warm_classification=_parse_enum(
                ExperimentPhase, raw["warm_classification"], field_name="warm_classification"
            ),
            comparison=(
                None if comparison_raw is None else ReuseComparison.from_dict(comparison_raw)
            ),
            verdict=_parse_enum(ReuseExperimentVerdict, raw["verdict"], field_name="verdict"),
            reasons=tuple(reasons_raw),
        )

    @classmethod
    def from_json(cls, text: str) -> ReuseExperimentResult:
        """Reconstruct one result from deterministic JSON."""

        if not isinstance(text, str):
            raise TypeError("reuse-experiment JSON must be a string")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ReuseExperimentDeserializationError("reuse-experiment JSON is malformed") from exc
        if not isinstance(raw, Mapping):
            raise ReuseExperimentDeserializationError(
                "reuse-experiment JSON root must be an object"
            )
        return cls.from_dict(raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReuseExperimentHarness:
    """Deterministic two-phase harness over injected execution ports.

    ``run`` executes the cold phase and then the warm phase — exactly one port
    call each — validates that each runner returned canonical evidence, and
    freezes the canonical M8.01 comparison into an inert result. The harness
    owns no clock, no model, no network, no persistence, no Procedure store,
    and no authority; it never selects which Procedure or route a phase uses.
    """

    cold_runner: PhaseRunner
    warm_runner: PhaseRunner

    def __post_init__(self) -> None:
        if not isinstance(self.cold_runner, PhaseRunner):
            raise TypeError("cold_runner must provide run_phase(request) port")
        if not isinstance(self.warm_runner, PhaseRunner):
            raise TypeError("warm_runner must provide run_phase(request) port")

    def run(self, spec: ReuseExperimentSpec) -> ReuseExperimentResult:
        """Measure one cold phase and one warm phase and compare them canonically."""

        if not isinstance(spec, ReuseExperimentSpec):
            raise TypeError("spec must be a ReuseExperimentSpec")
        cold = self._measure(
            self.cold_runner,
            phase=ExperimentPhase.COLD,
            experiment_id=spec.experiment_id,
            task_id=spec.cold_task_id,
        )
        warm = self._measure(
            self.warm_runner,
            phase=ExperimentPhase.WARM,
            experiment_id=spec.experiment_id,
            task_id=spec.warm_task_id,
        )
        components = _experiment_components(spec, cold, warm)
        return ReuseExperimentResult(
            spec=spec,
            cold=cold,
            warm=warm,
            cold_classification=components.cold_classification,
            warm_classification=components.warm_classification,
            comparison=components.comparison,
            verdict=components.verdict,
            reasons=components.reasons,
        )

    @staticmethod
    def _measure(
        runner: PhaseRunner,
        *,
        phase: ExperimentPhase,
        experiment_id: UUID,
        task_id: TaskId,
    ) -> ExecutionEfficiencyEvidence:
        """Invoke one injected port exactly once and require canonical evidence."""

        request = PhaseExecutionRequest(experiment_id=experiment_id, phase=phase, task_id=task_id)
        evidence = runner.run_phase(request)
        if not isinstance(evidence, ExecutionEfficiencyEvidence):
            raise PhaseEvidenceError(
                f"{phase.value} phase runner returned {type(evidence).__name__} "
                "instead of canonical ExecutionEfficiencyEvidence; "
                "the harness does not parse untyped data"
            )
        return evidence


__all__ = [
    "COLD_NO_REUSE_MODES",
    "REUSE_EXPERIMENT_SCHEMA_VERSION",
    "WARM_REUSE_MODES",
    "ExperimentPhase",
    "PhaseEvidenceError",
    "PhaseExecutionRequest",
    "PhaseRunner",
    "ReuseExperimentDeserializationError",
    "ReuseExperimentError",
    "ReuseExperimentHarness",
    "ReuseExperimentResult",
    "ReuseExperimentSpec",
    "ReuseExperimentVerdict",
    "UnsupportedReuseExperimentSchemaVersionError",
    "classify_reuse_mode",
]
