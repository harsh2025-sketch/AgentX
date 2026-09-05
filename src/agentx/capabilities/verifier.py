"""The AgentX Verifier boundary (A2.05).

The Verifier is the narrow runtime boundary through which higher orchestration
(later A2.06 Task Manager, A2.07 Router) *evaluates* one already-produced
canonical outcome against one explicitly supplied verification requirement.
It is deliberately the smallest possible component::

    VerifierRequest(outcome, requirement)   # both explicit, typed, immutable
        -> RequirementEvaluation            # a value, never a side effect

It is **not** a second capability-verification path
--------------------------------------------------

Deterministic capability execution and its per-invocation verification are
owned by A1.08/A1.10: :meth:`Capability.verify` produces the canonical
:class:`~agentx.capabilities.abi.VerificationResult`, and the canonical
:class:`~agentx.capabilities.runtime.CapabilityExecutionLoop` is the only
place that runs it. This module never invokes ``Capability.verify`` — or any
capability code — and it never manufactures a
:class:`~agentx.capabilities.abi.VerificationResult` and never rewrites a
:class:`~agentx.capabilities.runtime.ClosedLoopOutcome`. The A1.10 outcome it
receives is read-only evidence; the evaluation result it returns is a
separate, plain :class:`RequirementEvaluation` value.

It is **not** authority
-----------------------

The invariant is inherited, never re-implemented::

    NO ACTION == SUCCESS WITHOUT VERIFICATION.

The Verifier cannot turn observations, model text, or error strings into
machine success by assertion. Two structural rules enforce that:

    1. **Canonical verification evidence is a non-negotiable precondition.**
       ``satisfied=True`` requires the outcome to carry canonical A1.10
       verification evidence (``kind is LoopOutcome.VERIFIED`` and a
       ``verification`` verdict with ``passed=True``). An unverified, failed,
       or denied outcome can therefore never satisfy any requirement — even
       one whose expected values happen to match the observation.

    2. **The only additional evidence tests are explicit deterministic
       equalities.** The requirement states exact expected values for named
       observation-evidence keys. Evaluation is plain typed equality against
       the already-produced canonical evidence: no predicate code, no
       callables, no model calls, no schema language, no reinterpretation.

Hostile text is inert: strings such as ``"verified=true"``, ``"success"``, or
``"passed"`` carried in a summary, an error message, or observation data can
never flip ``satisfied``, because evaluation reads only typed canonical fields
(the :class:`~agentx.capabilities.runtime.LoopOutcome` enum and the
:class:`~agentx.capabilities.abi.VerificationResult` bool) and compares
explicitly expected typed values. Booleans never equal numbers, and strings
never equal anything but equal strings.

Fail closed
-----------

Malformed or missing evidence is never a success. A request whose evidence is
missing (no verdict, no observation) or of an unexpected type evaluates to
``satisfied=False`` with a deterministic unmet-condition string; malformed
requirements and requests of the wrong Python type are rejected at
construction. There are no hidden retries, no fallbacks, no loops beyond the
bounded walk over the requirement's own keys, and no state: the Verifier is
stateless, so identical inputs always produce an identical result.

Deliberate non-scope
--------------------

Not a Task Manager (A2.06), Router (A2.07), escalation (A2.08), anti-loop
(A2.09), or agent loop (A2.10). No capability execution, no
``Capability.verify`` invocation, no Task transition, no permission or
authority creation, no risk/budget/stop mutation, no event or audit
publication, no persistence, no reasoning, no research, no repair, and no
model-based critic.

Owner: A2.05. Belongs to ``agentx.capabilities`` — the canonical subsystem
that owns the outcome contracts it evaluates — so it adds no top-level package
and widens no boundary edge. It imports only the standard library and the
canonical A1.08/A1.10 sibling modules and the A1.05 JSON-value contract; it
does not import ``agentx.kernel`` at all, because it holds no authority and
touches no governed mechanism.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast

from agentx.capabilities.abi import CapabilityObservation, VerificationResult
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.tasks import JsonValue

__all__ = [
    "VERIFIER_SOURCE",
    "RequirementEvaluation",
    "VerificationRequirement",
    "Verifier",
    "VerifierRequest",
    "VerifierRequestError",
]

#: Stable identifier of this boundary, for reporting and documentation. The
#: Verifier publishes no events and writes no evidence of its own.
VERIFIER_SOURCE: Final[str] = "agentx.capabilities.verifier"

#: Maximum number of explicit expectation entries in one requirement. A bound
#: keeps evaluation time deterministic and rejection of oversized requirements
#: explicit rather than accidental.
_MAX_EXPECTATIONS: Final[int] = 64

#: Maximum length of one expectation key. Expectation keys name evidence
#: fields; they are bounded so they can be echoed safely in unmet conditions.
_MAX_KEY_LENGTH: Final[int] = 128

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class VerifierRequestError(ValueError):
    """Raised when a verification requirement carries malformed values.

    This is a request-shape error only. It never carries, encodes, or implies
    a verification verdict: verdicts about evidence are
    :class:`RequirementEvaluation` values, and the canonical A1.10 verdict
    remains owned by A1.10.
    """


def _validate_requirement_key(key: object) -> str:
    """Validate one explicit expectation key (bounded, safe to echo)."""
    if not isinstance(key, str):
        raise TypeError(f"expectation keys must be strings, got {type(key).__name__}")
    if not key or key != key.strip():
        raise VerifierRequestError("expectation keys must be non-empty and trimmed")
    if any(character in key for character in _CONTROL_CHARACTERS):
        raise VerifierRequestError("expectation keys must not contain control characters")
    if len(key) > _MAX_KEY_LENGTH:
        raise VerifierRequestError(f"expectation keys must not exceed {_MAX_KEY_LENGTH} characters")
    return key


def _freeze_json_value(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy.

    This mirrors the canonical JSON-value rules already enforced by the A1.08
    observation contract and the A1.05 Task metadata contract: ``None``, bool,
    int, finite float, str, mappings with string keys, and lists/tuples. Any
    other value — including callables, modules, or arbitrary objects — is
    rejected, so a requirement can never smuggle executable behaviour.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise VerifierRequestError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise VerifierRequestError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise VerifierRequestError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _values_match(expected: object, actual: object) -> bool:
    """Deterministic, type-strict equality between JSON-compatible values.

    Booleans match only booleans (``True`` is never ``1``), strings match only
    strings, ``None`` matches only ``None``, and numbers compare numerically
    (JSON makes no int/float distinction). Mappings compare as sets of keys
    with recursively matching values; sequences compare positionally
    regardless of the concrete list/tuple representation. Any other shape is
    a mismatch — never an error, never a partial success.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and bool(expected == actual)
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping) or len(expected) != len(actual):
            return False
        return all(
            key in actual and _values_match(matched, actual[key])
            for key, matched in expected.items()
        )
    if isinstance(expected, list | tuple):
        if not isinstance(actual, list | tuple):
            return False
        other = list(actual)
        if len(expected) != len(other):
            return False
        return all(
            _values_match(matched, candidate)
            for matched, candidate in zip(expected, other, strict=True)
        )
    if expected is None or actual is None:
        return expected is None and actual is None
    return bool(expected == actual)


@dataclass(frozen=True, slots=True)
class VerificationRequirement:
    """Explicit, deterministic verification requirement (A2.05 request side).

    The requirement states what the *already-produced* observation evidence
    must contain, as exact typed values keyed by evidence field name. It is
    data only: there is no predicate code, no callable, no schema language,
    and no free-text condition that some engine would have to interpret.

    ``expected_observation`` maps observation-evidence keys to the exact
    values those keys must carry. Evaluation compares with strict typed
    equality (see :func:`_values_match`). An empty mapping is allowed and
    means "no additional evidence conditions" — the Verifier still requires
    canonical A1.10 verification evidence unconditionally, so even an empty
    requirement is satisfied only by a canonically verified outcome.

    Values must be JSON-compatible (``None``, bool, int, finite float, str,
    and nested mappings/sequences of those). Anything executable is rejected
    at construction. The mapping is defensively frozen, so a requirement is
    immutable after construction.
    """

    expected_observation: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.expected_observation, Mapping):
            raise TypeError(
                "expected_observation must be a mapping of evidence key to expected "
                f"JSON-compatible value, got {type(self.expected_observation).__name__}"
            )
        if len(self.expected_observation) > _MAX_EXPECTATIONS:
            raise VerifierRequestError(
                f"expected_observation must not exceed {_MAX_EXPECTATIONS} entries"
            )
        for key in self.expected_observation:
            _validate_requirement_key(key)
        frozen = _freeze_json_value(self.expected_observation, path="expected_observation")
        object.__setattr__(
            self,
            "expected_observation",
            cast("Mapping[str, JsonValue]", frozen),
        )


@dataclass(frozen=True, slots=True)
class VerifierRequest:
    """The smallest typed evaluation request the Verifier accepts.

    It carries exactly the two canonical values evaluation needs and nothing
    else: the already-produced canonical A1.10
    :class:`~agentx.capabilities.runtime.ClosedLoopOutcome` (read-only
    evidence) and the explicit :class:`VerificationRequirement`.

    There is deliberately no permission field, no risk or budget override, no
    Task field, no strategy hint, no retry policy, no predicate callable, and
    no model/text field. Anything of that shape would be an authority decision
    or a second verification mechanism smuggled into a data object.
    """

    outcome: ClosedLoopOutcome
    requirement: VerificationRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ClosedLoopOutcome):
            raise TypeError(
                f"outcome must be a ClosedLoopOutcome, got {type(self.outcome).__name__}"
            )
        if not isinstance(self.requirement, VerificationRequirement):
            raise TypeError(
                "requirement must be a VerificationRequirement, got "
                f"{type(self.requirement).__name__}"
            )


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    """Immutable result of evaluating one outcome against one requirement.

    ``satisfied`` is ``True`` only when the outcome carried canonical A1.10
    verification evidence **and** every explicit expectation matched. It is
    not a :class:`~agentx.capabilities.abi.VerificationResult` (that verdict
    is manufactured only by A1.10), it grants no authority, transitions no
    Task, and rewrites nothing.

    ``unmet_conditions`` is empty exactly when ``satisfied`` is ``True``
    (enforced). Entries are deterministic, ordered, bounded strings naming
    what was missing or mismatched; they never echo evidence content, so
    hostile text cannot leak through them.
    """

    satisfied: bool
    unmet_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.satisfied) is not bool:
            raise TypeError(f"satisfied must be bool, got {type(self.satisfied).__name__}")
        if not isinstance(self.unmet_conditions, tuple) or any(
            not isinstance(condition, str) for condition in self.unmet_conditions
        ):
            raise TypeError("unmet_conditions must be a tuple of strings")
        if self.satisfied is not (len(self.unmet_conditions) == 0):
            raise VerifierRequestError(
                "satisfied must be True exactly when unmet_conditions is empty"
            )


class Verifier:
    """Evaluates already-produced canonical outcomes against requirements.

    The Verifier is a stateless boundary object: it holds no collaborators,
    no counters, no caches, and no histories. It cannot execute anything —
    the only operations it performs are typed field reads on the canonical
    outcome and deterministic equality checks against the explicit
    requirement. It imports no kernel module, so it has no path to
    permissions, risk, budgets, emergency stop, or audit authority.
    """

    __slots__ = ()

    def evaluate(self, request: VerifierRequest) -> RequirementEvaluation:
        """Evaluate one canonical outcome against one explicit requirement.

        The canonical outcome is read-only evidence: it is never rewritten,
        its Task is never transitioned, and its verification verdict is never
        manufactured or replaced. Evaluation is fail closed — missing,
        malformed, or type-inconsistent evidence produces ``satisfied=False``
        with a deterministic unmet condition — and fully deterministic: the
        same request always yields an equal
        :class:`RequirementEvaluation`.

        Raises :class:`TypeError` for a non-:class:`VerifierRequest`
        argument, which is a programming error rather than an evaluation
        outcome. Nothing else raises: evidence problems are unmet conditions,
        not exceptions, so a caller cannot accidentally treat an evaluation
        error as "not yet evaluated" and proceed.
        """
        if not isinstance(request, VerifierRequest):
            raise TypeError(f"request must be a VerifierRequest, got {type(request).__name__}")

        unmet: list[str] = []
        outcome = request.outcome

        # 1. Canonical verification evidence — non-negotiable, inherited from
        #    NO ACTION == SUCCESS WITHOUT VERIFICATION. The verdict itself is
        #    read, never manufactured; its type is confirmed before use so a
        #    malformed evidence object fails closed instead of crashing.
        verification: object = outcome.verification
        if verification is None:
            unmet.append("canonical verification evidence missing")
        elif not isinstance(verification, VerificationResult):
            unmet.append("canonical verification evidence has an unexpected type")
        elif verification.passed is not True:
            unmet.append("canonical verification did not pass")

        kind: object = outcome.kind
        if kind is not LoopOutcome.VERIFIED:
            if isinstance(kind, LoopOutcome):
                unmet.append(f"canonical outcome kind is {kind.value!r}, not 'verified'")
            else:
                unmet.append("canonical outcome kind has an unexpected type")

        observation: object = outcome.observation
        if observation is None:
            unmet.append("observation evidence missing")
        elif not isinstance(observation, CapabilityObservation):
            unmet.append("observation evidence has an unexpected type")

        # 2. Explicit deterministic evidence conditions. Keys are evaluated in
        #    sorted order so the unmet-condition list is deterministic
        #    regardless of requirement construction order.
        if isinstance(observation, CapabilityObservation):
            data = observation.data
            for key in sorted(request.requirement.expected_observation):
                expected = request.requirement.expected_observation[key]
                if key not in data:
                    unmet.append(f"observation evidence key {key!r} is missing")
                elif not _values_match(expected, data[key]):
                    unmet.append(
                        f"observation evidence key {key!r} does not match the required value"
                    )

        return RequirementEvaluation(
            satisfied=not unmet,
            unmet_conditions=tuple(unmet),
        )
