"""Governed safe self-extension and capability acquisition for AgentX M15.

The M15 boundary deliberately supports only side-effect-free generated tools in a
small interpreted Python subset. Candidate text is data, never authority. It is
never imported into the trusted process and never receives filesystem, network,
subprocess, environment, kernel, registry, approval, or verifier objects.

Promotion is a trusted lifecycle transition bound to immutable artifact digests,
validation evidence, repeated varied successes, a human-review package, and an
HMAC-authenticated installation approval. Active generated capabilities still
execute through the canonical Capability Registry -> ActionGate -> Executor path.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID, uuid4

import agentx.kernel as kernel_package
from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.registry import CapabilityAlreadyRegisteredError, CapabilityRegistry
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment, RiskLevel, assess_risk

__all__ = [
    "CandidateArtifact",
    "CandidateLifecycle",
    "CandidateProvenance",
    "CandidateSourceType",
    "CapabilityDesignProposal",
    "CapabilityGapReason",
    "CapabilityGapRecord",
    "CapabilityResearchObjective",
    "DependencyPolicy",
    "ExtensionHealthState",
    "GeneratedCapability",
    "GeneratedCapabilityParams",
    "GeneratedDependency",
    "GeneratedToolSandbox",
    "GeneratedToolSpecification",
    "HumanReviewPackage",
    "InstallationApproval",
    "OutputField",
    "OutputType",
    "SandboxPolicy",
    "SandboxResult",
    "SelfExtensionError",
    "SelfExtensionManager",
    "SelfExtensionSecurityError",
    "StaticAnalysisReport",
    "TrustedApprovalAuthority",
    "ValidationCase",
    "ValidationEvidence",
    "ValidationKind",
    "classify_runtime_failure",
    "trusted_kernel_fingerprint",
    "validate_candidate",
]

_SCHEMA_VERSION: Final[int] = 1
_MAX_SOURCE_BYTES: Final[int] = 32_768
_MAX_PAYLOAD_BYTES: Final[int] = 65_536
_MAX_OUTPUT_BYTES: Final[int] = 65_536
_MAX_AST_NODES: Final[int] = 512
_MAX_EVAL_STEPS: Final[int] = 2_048
_MIN_PROMOTION_SUCCESSES: Final[int] = 3
_MAX_TEXT: Final[int] = 1_024
_DIGEST_PREFIX: Final[str] = "sha256:"
_ALLOWED_CALLS: Final[frozenset[str]] = frozenset(
    {"abs", "bool", "float", "int", "len", "max", "min", "round", "sorted", "str", "sum"}
)


class SelfExtensionError(ValueError):
    """Malformed M15 contract or rejected lifecycle transition."""


class SelfExtensionSecurityError(SelfExtensionError):
    """Security validation or persistence-integrity failure."""


def _text(value: object, *, name: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise SelfExtensionError(f"{name} must be non-empty and trimmed")
    if len(value) > maximum or any(ch in value for ch in ("\x00", "\r", "\n")):
        raise SelfExtensionError(f"{name} is invalid or too long")
    return value


def _aware(value: object, *, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SelfExtensionError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _digest_bytes(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _validate_digest(value: object, *, name: str) -> str:
    raw = _text(value, name=name, maximum=80)
    if not raw.startswith(_DIGEST_PREFIX) or len(raw) != len(_DIGEST_PREFIX) + 64:
        raise SelfExtensionError(f"{name} must be a sha256 digest")
    try:
        bytes.fromhex(raw.removeprefix(_DIGEST_PREFIX))
    except ValueError as exc:
        raise SelfExtensionError(f"{name} must be hexadecimal") from exc
    return raw


def _identity_dict(identity: CapabilityIdentity) -> dict[str, object]:
    return {
        "name": identity.name.value,
        "version": {
            "major": identity.version.major,
            "minor": identity.version.minor,
            "patch": identity.version.patch,
        },
    }


def _artifact_digest(
    *,
    proposal: CapabilityDesignProposal,
    source: str,
    dependencies: tuple[GeneratedDependency, ...],
) -> str:
    material = {
        "identity": _identity_dict(proposal.identity),
        "proposal_id": str(proposal.proposal_id),
        "source": source,
        "dependencies": [
            {"name": item.name, "version": item.version, "digest": item.digest}
            for item in dependencies
        ],
    }
    return _digest_bytes(_canonical_json(material))


def _identity_from_dict(raw: Mapping[str, object]) -> CapabilityIdentity:
    name = raw.get("name")
    version = raw.get("version")
    if not isinstance(name, str) or not isinstance(version, Mapping):
        raise SelfExtensionError("stored capability identity is malformed")
    major = version.get("major")
    minor = version.get("minor")
    patch = version.get("patch")
    if type(major) is not int or type(minor) is not int or type(patch) is not int:
        raise SelfExtensionError("stored capability version is malformed")
    return CapabilityIdentity(
        name=CapabilityName(name),
        version=CapabilityVersion(major, minor, patch),
    )


class CapabilityGapReason(StrEnum):
    MISSING = "capability_missing"
    FAILED = "capability_failed"
    PERMISSION_DENIED = "permission_denied"
    RISK_REJECTED = "risk_rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEVICE_UNAVAILABLE = "device_unavailable"
    PROCEDURE_NOT_APPLICABLE = "procedure_not_applicable"
    TRANSIENT_FAILURE = "transient_failure"
    VERIFICATION_FAILURE = "verification_failure"
    INVALID_REQUEST = "invalid_request"
    CANCELLED = "cancelled"
    EMERGENCY_STOP = "emergency_stop"

    @property
    def extension_eligible(self) -> bool:
        return self is CapabilityGapReason.MISSING


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityGapRecord:
    gap_id: UUID
    required: CapabilityIdentity
    reason: CapabilityGapReason
    requesting_task: str
    source_error_code: str | None = None
    platform: CapabilityPlatform = CapabilityPlatform.ANY
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.gap_id, UUID):
            raise TypeError("gap_id must be UUID")
        if not isinstance(self.required, CapabilityIdentity):
            raise TypeError("required must be CapabilityIdentity")
        if not isinstance(self.reason, CapabilityGapReason):
            raise TypeError("reason must be CapabilityGapReason")
        _text(self.requesting_task, name="requesting_task")
        if self.source_error_code is not None:
            _text(self.source_error_code, name="source_error_code")
        if not isinstance(self.platform, CapabilityPlatform):
            raise TypeError("platform must be CapabilityPlatform")
        object.__setattr__(self, "observed_at", _aware(self.observed_at, name="observed_at"))


def classify_runtime_failure(
    *,
    required: CapabilityIdentity,
    requesting_task: str,
    error: AgentXError,
    observed_at: datetime,
    platform: CapabilityPlatform = CapabilityPlatform.ANY,
) -> CapabilityGapRecord:
    """Classify a canonical runtime failure without turning denials into gaps."""
    if not isinstance(error, AgentXError):
        raise TypeError("error must be AgentXError")
    code = error.code
    if code == "runtime.capability_not_registered" or error.category is ErrorCategory.NOT_FOUND:
        reason = CapabilityGapReason.MISSING
    elif code == "runtime.emergency_stop_active":
        reason = CapabilityGapReason.EMERGENCY_STOP
    elif error.category is ErrorCategory.PERMISSION:
        reason = (
            CapabilityGapReason.RISK_REJECTED
            if "gate" in code or "risk" in code
            else CapabilityGapReason.PERMISSION_DENIED
        )
    elif error.category is ErrorCategory.RESOURCE:
        reason = CapabilityGapReason.BUDGET_EXHAUSTED
    elif error.category is ErrorCategory.CANCELLED:
        reason = CapabilityGapReason.CANCELLED
    elif error.category is ErrorCategory.VERIFICATION:
        reason = CapabilityGapReason.VERIFICATION_FAILURE
    elif error.category in (ErrorCategory.TIMEOUT, ErrorCategory.DEPENDENCY):
        reason = CapabilityGapReason.TRANSIENT_FAILURE
    elif error.category in (ErrorCategory.VALIDATION, ErrorCategory.PRECONDITION):
        reason = CapabilityGapReason.INVALID_REQUEST
    else:
        reason = CapabilityGapReason.FAILED
    return CapabilityGapRecord(
        gap_id=uuid4(),
        required=required,
        reason=reason,
        requesting_task=requesting_task,
        source_error_code=code,
        platform=platform,
        observed_at=observed_at,
    )


class CandidateSourceType(StrEnum):
    LOCAL_REGISTRY = "local_registry"
    APPROVED_REGISTRY = "approved_registry"
    PROCEDURE = "procedure"
    GENERATED = "generated"
    SOURCE_REPOSITORY = "source_repository"
    PACKAGE = "package"
    MODEL_ASSISTED = "model_assisted"
    RESEARCH_ASSISTED = "research_assisted"


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityResearchObjective:
    objective_id: UUID
    gap: CapabilityGapRecord
    allowed_sources: tuple[CandidateSourceType, ...]
    constraints: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.objective_id, UUID):
            raise TypeError("objective_id must be UUID")
        if not isinstance(self.gap, CapabilityGapRecord):
            raise TypeError("gap must be CapabilityGapRecord")
        if not self.gap.reason.extension_eligible:
            raise SelfExtensionSecurityError(
                f"{self.gap.reason.value} cannot trigger capability acquisition"
            )
        if not self.allowed_sources or len(set(self.allowed_sources)) != len(self.allowed_sources):
            raise SelfExtensionError("allowed_sources must be non-empty and unique")
        for source in self.allowed_sources:
            if not isinstance(source, CandidateSourceType):
                raise TypeError("allowed_sources must contain CandidateSourceType")
        if not isinstance(self.constraints, tuple):
            raise TypeError("constraints must be tuple")
        for constraint in self.constraints:
            _text(constraint, name="constraint")
        object.__setattr__(self, "created_at", _aware(self.created_at, name="created_at"))


class OutputType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    NULL = "null"


@dataclass(frozen=True, slots=True)
class OutputField:
    name: str
    value_type: OutputType

    def __post_init__(self) -> None:
        _text(self.name, name="output field", maximum=128)
        if not isinstance(self.value_type, OutputType):
            raise TypeError("value_type must be OutputType")


@dataclass(frozen=True, slots=True)
class GeneratedToolSpecification:
    input_keys: tuple[str, ...]
    output_fields: tuple[OutputField, ...]
    side_effect_free: bool = True
    deterministic: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.input_keys, tuple) or len(set(self.input_keys)) != len(
            self.input_keys
        ):
            raise SelfExtensionError("input_keys must be a unique tuple")
        for key in self.input_keys:
            _text(key, name="input key", maximum=128)
        if not isinstance(self.output_fields, tuple) or not self.output_fields:
            raise SelfExtensionError("output_fields must be a non-empty tuple")
        names = [field.name for field in self.output_fields]
        if len(names) != len(set(names)):
            raise SelfExtensionError("output field names must be unique")
        if type(self.side_effect_free) is not bool or type(self.deterministic) is not bool:
            raise TypeError("side_effect_free and deterministic must be bool")
        if not self.side_effect_free or not self.deterministic:
            raise SelfExtensionSecurityError(
                "M15 generated tools must be deterministic and side-effect-free"
            )

    def validate_input(self, payload: Mapping[str, object]) -> None:
        if set(payload) != set(self.input_keys):
            raise SelfExtensionError("payload keys do not match the generated-tool specification")

    def validate_output(self, output: object) -> bool:
        if not isinstance(output, Mapping):
            return False
        if set(output) != {field.name for field in self.output_fields}:
            return False
        for field in self.output_fields:
            value = output[field.name]
            if field.value_type is OutputType.STRING and not isinstance(value, str):
                return False
            if field.value_type is OutputType.INTEGER and (
                type(value) is not int
            ):
                return False
            if field.value_type is OutputType.NUMBER and (
                type(value) not in (int, float)
            ):
                return False
            if field.value_type is OutputType.BOOLEAN and type(value) is not bool:
                return False
            if field.value_type is OutputType.OBJECT and not isinstance(value, Mapping):
                return False
            if field.value_type is OutputType.ARRAY and not isinstance(value, list | tuple):
                return False
            if field.value_type is OutputType.NULL and value is not None:
                return False
        return True


@dataclass(frozen=True, slots=True)
class GeneratedDependency:
    name: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        _text(self.name, name="dependency.name", maximum=128)
        _text(self.version, name="dependency.version", maximum=64)
        object.__setattr__(
            self,
            "digest",
            _validate_digest(self.digest, name="dependency.digest"),
        )


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    approved: tuple[GeneratedDependency, ...] = ()

    def validate(self, dependencies: tuple[GeneratedDependency, ...]) -> tuple[str, ...]:
        if not isinstance(dependencies, tuple):
            raise TypeError("dependencies must be tuple")
        keys = [(item.name, item.version, item.digest) for item in dependencies]
        if len(keys) != len(set(keys)):
            return ("duplicate dependency",)
        approved = {(item.name, item.version, item.digest) for item in self.approved}
        violations = [
            f"dependency {item.name}@{item.version} is not pinned to an approved digest"
            for item in dependencies
            if (item.name, item.version, item.digest) not in approved
        ]
        return tuple(violations)


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityDesignProposal:
    proposal_id: UUID
    objective_id: UUID
    identity: CapabilityIdentity
    description: str
    platform: CapabilityPlatform
    required_permissions: frozenset[Permission]
    risk_assessment: RiskAssessment
    specification: GeneratedToolSpecification
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, UUID) or not isinstance(self.objective_id, UUID):
            raise TypeError("proposal_id and objective_id must be UUID")
        if not isinstance(self.identity, CapabilityIdentity):
            raise TypeError("identity must be CapabilityIdentity")
        _text(self.description, name="description")
        if not isinstance(self.platform, CapabilityPlatform):
            raise TypeError("platform must be CapabilityPlatform")
        if not isinstance(self.required_permissions, frozenset):
            raise TypeError("required_permissions must be frozenset")
        for permission in self.required_permissions:
            if not isinstance(permission, Permission):
                raise TypeError("required_permissions must contain Permission")
        if not isinstance(self.risk_assessment, RiskAssessment):
            raise TypeError("risk_assessment must be RiskAssessment")
        if not isinstance(self.specification, GeneratedToolSpecification):
            raise TypeError("specification must be GeneratedToolSpecification")
        object.__setattr__(self, "created_at", _aware(self.created_at, name="created_at"))

        # Generated tools are intentionally pure. A candidate cannot smuggle host
        # authority through metadata, risk downgrades, or permission declarations.
        if self.required_permissions - {Permission.READ}:
            raise SelfExtensionSecurityError(
                "generated tools may request at most READ; host mutation requires a native provider"
            )
        if self.risk_assessment.effective_level is not RiskLevel.R0:
            raise SelfExtensionSecurityError("generated tools must remain effective risk R0")


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateProvenance:
    source_type: CandidateSourceType
    source: str
    retrieved_at: datetime
    generator: str | None = None
    parent_digest: str | None = None
    requesting_task: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, CandidateSourceType):
            raise TypeError("source_type must be CandidateSourceType")
        _text(self.source, name="source")
        object.__setattr__(self, "retrieved_at", _aware(self.retrieved_at, name="retrieved_at"))
        if self.generator is not None:
            _text(self.generator, name="generator")
        if self.parent_digest is not None:
            object.__setattr__(
                self,
                "parent_digest",
                _validate_digest(self.parent_digest, name="parent_digest"),
            )
        if self.requesting_task is not None:
            _text(self.requesting_task, name="requesting_task")


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateArtifact:
    artifact_id: UUID
    proposal: CapabilityDesignProposal
    source: str
    provenance: CandidateProvenance
    dependencies: tuple[GeneratedDependency, ...]
    digest: str

    @classmethod
    def create(
        cls,
        *,
        proposal: CapabilityDesignProposal,
        source: str,
        provenance: CandidateProvenance,
        dependencies: tuple[GeneratedDependency, ...] = (),
    ) -> CandidateArtifact:
        if not isinstance(proposal, CapabilityDesignProposal):
            raise TypeError("proposal must be CapabilityDesignProposal")
        if not isinstance(provenance, CandidateProvenance):
            raise TypeError("provenance must be CandidateProvenance")
        if not isinstance(source, str):
            raise TypeError("source must be string")
        encoded = source.encode("utf-8")
        if not encoded or len(encoded) > _MAX_SOURCE_BYTES:
            raise SelfExtensionError("candidate source is empty or exceeds the source limit")
        return cls(
            artifact_id=uuid4(),
            proposal=proposal,
            source=source,
            provenance=provenance,
            dependencies=dependencies,
            digest=_artifact_digest(
                proposal=proposal,
                source=source,
                dependencies=dependencies,
            ),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, UUID):
            raise TypeError("artifact_id must be UUID")
        if not isinstance(self.proposal, CapabilityDesignProposal):
            raise TypeError("proposal must be CapabilityDesignProposal")
        if not isinstance(self.provenance, CandidateProvenance):
            raise TypeError("provenance must be CandidateProvenance")
        if not isinstance(self.dependencies, tuple):
            raise TypeError("dependencies must be tuple")
        object.__setattr__(self, "digest", _validate_digest(self.digest, name="artifact.digest"))
        expected = _artifact_digest(
            proposal=self.proposal,
            source=self.source,
            dependencies=self.dependencies,
        )
        if not hmac.compare_digest(self.digest, expected):
            raise SelfExtensionSecurityError("candidate artifact digest does not match its bytes")


class CandidateLifecycle(StrEnum):
    CANDIDATE = "candidate"
    INSPECTED = "inspected"
    VALIDATING = "validating"
    VALIDATED = "validated"
    PROMOTABLE = "promotable"
    ACTIVE = "active"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    DEGRADED = "degraded"
    REVOKED = "revoked"
    RETIRED = "retired"


class ValidationKind(StrEnum):
    UNIT = "unit"
    ADVERSARIAL = "adversarial"
    VARIATION = "variation"


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationCase:
    name: str
    payload: Mapping[str, object]
    expected: Mapping[str, object]
    kind: ValidationKind = ValidationKind.UNIT

    def __post_init__(self) -> None:
        _text(self.name, name="validation case", maximum=128)
        if not isinstance(self.kind, ValidationKind):
            raise TypeError("kind must be ValidationKind")
        payload = json.loads(_canonical_json(dict(self.payload)).decode("utf-8"))
        expected = json.loads(_canonical_json(dict(self.expected)).decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(expected, dict):
            raise SelfExtensionError("validation payload and expected output must be JSON objects")
        if len(_canonical_json(payload)) > _MAX_PAYLOAD_BYTES:
            raise SelfExtensionError("validation payload exceeds limit")
        if len(_canonical_json(expected)) > _MAX_OUTPUT_BYTES:
            raise SelfExtensionError("validation expected output exceeds limit")
        object.__setattr__(self, "payload", MappingProxyType(payload))
        object.__setattr__(self, "expected", MappingProxyType(expected))

    @property
    def case_digest(self) -> str:
        return _digest_bytes(
            _canonical_json(
                {
                    "name": self.name,
                    "payload": dict(self.payload),
                    "expected": dict(self.expected),
                    "kind": self.kind.value,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class StaticAnalysisReport:
    artifact_digest: str
    violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


_ALLOWED_AST_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Dict,
    ast.List,
    ast.Tuple,
    ast.Subscript,
    ast.Slice,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.keyword,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


def _parse_candidate_source(source: str) -> tuple[ast.FunctionDef | None, tuple[str, ...]]:
    violations: list[str] = []
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError:
        return None, ("source is not valid Python syntax",)
    nodes = tuple(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        violations.append("source exceeds the AST node limit")
    for node in nodes:
        if not isinstance(node, _ALLOWED_AST_NODES):
            violations.append(f"forbidden AST node: {type(node).__name__}")
        if (
            isinstance(node, ast.Name)
            and node.id != "payload"
            and node.id not in _ALLOWED_CALLS
        ):
            violations.append(f"forbidden name: {node.id}")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS
        ):
            violations.append("only approved pure builtin calls are allowed")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and len(node.value) > 4_096:
                violations.append("string literal exceeds limit")
            if type(node.value) is int and abs(node.value) > 1_000_000_000_000:
                violations.append("integer literal exceeds limit")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        violations.append("source must contain exactly one function")
        return None, tuple(sorted(set(violations)))
    function = tree.body[0]
    if function.name != "run":
        violations.append("generated function must be named run")
    if function.decorator_list:
        violations.append("decorators are forbidden")
    args = function.args
    if (
        len(args.posonlyargs) != 0
        or len(args.args) != 1
        or args.args[0].arg != "payload"
        or args.vararg is not None
        or args.kwarg is not None
        or args.kwonlyargs
        or args.defaults
        or args.kw_defaults
    ):
        violations.append("run must accept exactly one positional argument named payload")
    if len(function.body) != 1 or not isinstance(function.body[0], ast.Return):
        violations.append("run body must be exactly one return expression")
    return function, tuple(sorted(set(violations)))


def inspect_candidate(artifact: CandidateArtifact) -> StaticAnalysisReport:
    function, violations = _parse_candidate_source(artifact.source)
    extra = list(violations)
    if function is not None:
        # AST has no Import/Attribute/Assign/With/Try/Class/loop nodes under the allowlist.
        # This explicit scan documents the authority boundary in the evidence.
        forbidden_names = {
            "__import__",
            "compile",
            "eval",
            "exec",
            "getattr",
            "globals",
            "locals",
            "open",
            "setattr",
        }
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                extra.append(f"forbidden authority primitive: {node.id}")
    return StaticAnalysisReport(
        artifact_digest=artifact.digest,
        violations=tuple(sorted(set(extra))),
    )


def lint_candidate(artifact: CandidateArtifact) -> tuple[str, ...]:
    violations: list[str] = []
    for number, line in enumerate(artifact.source.splitlines(), start=1):
        if "\t" in line:
            violations.append(f"line {number}: tabs are forbidden")
        if line.rstrip() != line:
            violations.append(f"line {number}: trailing whitespace")
        if len(line) > 100:
            violations.append(f"line {number}: line too long")
    return tuple(violations)


def typecheck_candidate(artifact: CandidateArtifact) -> tuple[str, ...]:
    function, violations = _parse_candidate_source(artifact.source)
    if violations or function is None:
        return violations or ("missing run function",)
    # M15 deliberately uses a single typed JSON-object ABI. Source annotations are
    # optional inert text; the trusted wrapper owns the real CapabilityParams type.
    return ()


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    timeout_seconds: float = 2.0
    max_input_bytes: int = _MAX_PAYLOAD_BYTES
    max_output_bytes: int = _MAX_OUTPUT_BYTES
    max_steps: int = _MAX_EVAL_STEPS
    network_allowed: bool = False
    filesystem_allowed: bool = False
    subprocess_allowed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.timeout_seconds, int | float) or self.timeout_seconds <= 0:
            raise SelfExtensionError("timeout_seconds must be positive")
        for name in ("max_input_bytes", "max_output_bytes", "max_steps"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise SelfExtensionError(f"{name} must be a positive int")
        if self.network_allowed or self.filesystem_allowed or self.subprocess_allowed:
            raise SelfExtensionSecurityError("M15 sandbox cannot enable host authority")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    succeeded: bool
    output: object | None
    error_code: str | None
    transcript_digest: str
    artifact_digest: str

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise TypeError("succeeded must be bool")
        object.__setattr__(
            self,
            "transcript_digest",
            _validate_digest(self.transcript_digest, name="transcript_digest"),
        )
        object.__setattr__(
            self,
            "artifact_digest",
            _validate_digest(self.artifact_digest, name="artifact_digest"),
        )
        if self.succeeded and self.error_code is not None:
            raise SelfExtensionError("successful sandbox result cannot have error_code")
        if not self.succeeded and self.error_code is None:
            raise SelfExtensionError("failed sandbox result requires error_code")


class _StepBudget:
    __slots__ = ("remaining",)

    def __init__(self, maximum: int) -> None:
        self.remaining = maximum

    def take(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise SelfExtensionSecurityError("sandbox step limit exceeded")


def _bounded(value: object, *, maximum: int) -> object:
    try:
        encoded = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise SelfExtensionSecurityError("sandbox value is not JSON-compatible") from exc
    if len(encoded) > maximum:
        raise SelfExtensionSecurityError("sandbox value exceeds size limit")
    return value


def _eval_expr(node: ast.expr, payload: Mapping[str, object], budget: _StepBudget) -> object:
    budget.take()
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "payload":
            return dict(payload)
        raise SelfExtensionSecurityError("bare builtin names are not values")
    if isinstance(node, ast.Dict):
        keys = [_eval_expr(cast(ast.expr, item), payload, budget) for item in node.keys]
        values = [_eval_expr(item, payload, budget) for item in node.values]
        if any(not isinstance(key, str) for key in keys):
            raise SelfExtensionSecurityError("generated output dictionary keys must be strings")
        return dict(zip(cast(list[str], keys), values, strict=True))
    if isinstance(node, ast.List):
        return [_eval_expr(item, payload, budget) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return [_eval_expr(item, payload, budget) for item in node.elts]
    if isinstance(node, ast.Subscript):
        target = _eval_expr(node.value, payload, budget)
        index = _eval_expr(cast(ast.expr, node.slice), payload, budget)
        if not isinstance(target, Mapping | list | tuple | str):
            raise SelfExtensionSecurityError("subscript target type is forbidden")
        if isinstance(target, Mapping):
            if not isinstance(index, str):
                raise SelfExtensionSecurityError("mapping subscripts must be strings")
            return target[index]
        if type(index) is not int:
            raise SelfExtensionSecurityError("sequence subscripts must be integers")
        return target[index]
    if isinstance(node, ast.UnaryOp):
        value = _eval_expr(node.operand, payload, budget)
        if isinstance(node.op, ast.Not):
            return not bool(value)
        if type(value) not in (int, float):
            raise SelfExtensionSecurityError("numeric unary operator requires a number")
        return +value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval_expr(node.left, payload, budget)
        right = _eval_expr(node.right, payload, budget)
        if isinstance(node.op, ast.Add):
            result = left + right  # type: ignore[operator]
        elif isinstance(node.op, ast.Sub):
            result = left - right  # type: ignore[operator]
        elif isinstance(node.op, ast.Mult):
            if isinstance(left, (str, list)) and type(right) is int and abs(right) > 16:
                raise SelfExtensionSecurityError("sequence multiplication exceeds limit")
            if isinstance(right, (str, list)) and type(left) is int and abs(left) > 16:
                raise SelfExtensionSecurityError("sequence multiplication exceeds limit")
            result = left * right  # type: ignore[operator]
        elif isinstance(node.op, ast.Div):
            result = left / right  # type: ignore[operator]
        elif isinstance(node.op, ast.FloorDiv):
            result = left // right  # type: ignore[operator]
        elif isinstance(node.op, ast.Mod):
            result = left % right  # type: ignore[operator]
        elif isinstance(node.op, ast.Pow):
            if type(right) is not int or abs(right) > 10:
                raise SelfExtensionSecurityError("power exponent exceeds limit")
            result = left**right  # type: ignore[operator]
        else:
            raise SelfExtensionSecurityError("binary operator is forbidden")
        return _bounded(result, maximum=_MAX_OUTPUT_BYTES)
    if isinstance(node, ast.BoolOp):
        values = [_eval_expr(item, payload, budget) for item in node.values]
        return all(bool(item) for item in values) if isinstance(node.op, ast.And) else any(
            bool(item) for item in values
        )
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise SelfExtensionSecurityError("chained comparisons are forbidden")
        left = _eval_expr(node.left, payload, budget)
        right = _eval_expr(node.comparators[0], payload, budget)
        op = node.ops[0]
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left < right  # type: ignore[operator]
        if isinstance(op, ast.LtE):
            return left <= right  # type: ignore[operator]
        if isinstance(op, ast.Gt):
            return left > right  # type: ignore[operator]
        if isinstance(op, ast.GtE):
            return left >= right  # type: ignore[operator]
        if isinstance(op, ast.In):
            return left in right  # type: ignore[operator]
        if isinstance(op, ast.NotIn):
            return left not in right  # type: ignore[operator]
        raise SelfExtensionSecurityError("comparison operator is forbidden")
    if isinstance(node, ast.IfExp):
        condition = _eval_expr(node.test, payload, budget)
        branch = node.body if bool(condition) else node.orelse
        return _eval_expr(branch, payload, budget)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
            raise SelfExtensionSecurityError("call target is forbidden")
        args = [_eval_expr(arg, payload, budget) for arg in node.args]
        if node.keywords:
            raise SelfExtensionSecurityError("keyword arguments are forbidden")
        functions: dict[str, object] = {
            "abs": abs,
            "bool": bool,
            "float": float,
            "int": int,
            "len": len,
            "max": max,
            "min": min,
            "round": round,
            "sorted": sorted,
            "str": str,
            "sum": sum,
        }
        function = functions[node.func.id]
        result = function(*args)  # type: ignore[operator]
        return _bounded(result, maximum=_MAX_OUTPUT_BYTES)
    raise SelfExtensionSecurityError(f"expression node {type(node).__name__} is forbidden")


def execute_safe_candidate(
    *,
    source: str,
    payload: Mapping[str, object],
    max_steps: int = _MAX_EVAL_STEPS,
) -> object:
    """Interpret one already-bounded candidate without eval/exec/import."""
    function, violations = _parse_candidate_source(source)
    if violations or function is None or not isinstance(function.body[0], ast.Return):
        raise SelfExtensionSecurityError("candidate failed static inspection")
    expression = function.body[0].value
    if expression is None:
        raise SelfExtensionSecurityError("candidate must return a value")
    result = _eval_expr(expression, payload, _StepBudget(max_steps))
    return _bounded(result, maximum=_MAX_OUTPUT_BYTES)


class GeneratedToolSandbox:
    """Separate-process safe-subset evaluator with a deny-by-construction authority surface."""

    __slots__ = ("_policy",)

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        resolved = SandboxPolicy() if policy is None else policy
        if not isinstance(resolved, SandboxPolicy):
            raise TypeError("policy must be SandboxPolicy or None")
        self._policy = resolved

    @property
    def policy(self) -> SandboxPolicy:
        return self._policy

    def run(self, artifact: CandidateArtifact, payload: Mapping[str, object]) -> SandboxResult:
        analysis = inspect_candidate(artifact)
        transcript_material = {
            "artifact_digest": artifact.digest,
            "payload": dict(payload),
        }
        transcript_digest = _digest_bytes(_canonical_json(transcript_material))
        if not analysis.passed:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.static_analysis_failed",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        payload_bytes = _canonical_json(dict(payload))
        if len(payload_bytes) > self._policy.max_input_bytes:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.input_limit",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        envelope = {
            "artifact_digest": artifact.digest,
            "source": artifact.source,
            "payload": dict(payload),
            "max_steps": self._policy.max_steps,
        }
        child_env = {
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        system_root = os.environ.get("SYSTEMROOT")
        if system_root:
            child_env["SYSTEMROOT"] = system_root
        try:
            with tempfile.TemporaryDirectory(prefix="agentx-m15-") as working_dir:
                completed = subprocess.run(
                    [sys.executable, "-I", "-m", "agentx.self_extension_worker"],
                    input=_canonical_json(envelope),
                    capture_output=True,
                    cwd=working_dir,
                    env=child_env,
                    timeout=self._policy.timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.sandbox_timeout",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        if completed.returncode != 0 or len(completed.stdout) > self._policy.max_output_bytes:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.sandbox_failed",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        try:
            decoded = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.sandbox_protocol",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        if not isinstance(decoded, Mapping) or decoded.get("ok") is not True:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.candidate_failed",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        output = decoded.get("output")
        try:
            _bounded(output, maximum=self._policy.max_output_bytes)
        except SelfExtensionSecurityError:
            return SandboxResult(
                succeeded=False,
                output=None,
                error_code="extension.output_limit",
                transcript_digest=transcript_digest,
                artifact_digest=artifact.digest,
            )
        final_digest = _digest_bytes(
            _canonical_json(
                {
                    "artifact_digest": artifact.digest,
                    "payload": dict(payload),
                    "output": output,
                }
            )
        )
        return SandboxResult(
            succeeded=True,
            output=output,
            error_code=None,
            transcript_digest=final_digest,
            artifact_digest=artifact.digest,
        )


def trusted_kernel_fingerprint() -> str:
    """Hash the on-disk Trusted Kernel Python sources as immutable security evidence."""
    package_file = kernel_package.__file__
    if package_file is None:
        raise SelfExtensionSecurityError("Trusted Kernel package has no source location")
    root = Path(package_file).resolve().parent
    payload: list[tuple[str, str]] = []
    for path in sorted(root.glob("*.py")):
        payload.append((path.name, _digest_bytes(path.read_bytes())))
    if not payload:
        raise SelfExtensionSecurityError("Trusted Kernel source set is empty")
    return _digest_bytes(_canonical_json(payload))


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationEvidence:
    artifact_digest: str
    static_passed: bool
    typecheck_passed: bool
    lint_passed: bool
    dependency_passed: bool
    unit_successes: int
    adversarial_successes: int
    variation_successes: int
    case_digests: tuple[str, ...]
    sandboxed: bool
    network_isolated: bool
    filesystem_isolated: bool
    resource_limited: bool
    kernel_before: str
    kernel_after: str
    validated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_digest",
            _validate_digest(self.artifact_digest, name="artifact_digest"),
        )
        for name in (
            "static_passed",
            "typecheck_passed",
            "lint_passed",
            "dependency_passed",
            "sandboxed",
            "network_isolated",
            "filesystem_isolated",
            "resource_limited",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")
        for name in ("unit_successes", "adversarial_successes", "variation_successes"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise SelfExtensionError(f"{name} must be a non-negative int")
        for digest in self.case_digests:
            _validate_digest(digest, name="case_digest")
        object.__setattr__(
            self,
            "kernel_before",
            _validate_digest(self.kernel_before, name="kernel_before"),
        )
        object.__setattr__(
            self,
            "kernel_after",
            _validate_digest(self.kernel_after, name="kernel_after"),
        )
        object.__setattr__(self, "validated_at", _aware(self.validated_at, name="validated_at"))

    @property
    def total_successes(self) -> int:
        return self.unit_successes + self.adversarial_successes + self.variation_successes

    @property
    def passed(self) -> bool:
        return (
            self.static_passed
            and self.typecheck_passed
            and self.lint_passed
            and self.dependency_passed
            and self.sandboxed
            and self.network_isolated
            and self.filesystem_isolated
            and self.resource_limited
            and self.kernel_before == self.kernel_after
            and self.total_successes >= _MIN_PROMOTION_SUCCESSES
            and len(set(self.case_digests)) >= _MIN_PROMOTION_SUCCESSES
        )


def validate_candidate(
    *,
    artifact: CandidateArtifact,
    cases: Sequence[ValidationCase],
    sandbox: GeneratedToolSandbox,
    dependency_policy: DependencyPolicy,
    validated_at: datetime,
) -> ValidationEvidence:
    if not cases:
        raise SelfExtensionError("candidate validation requires varied test cases")
    before = trusted_kernel_fingerprint()
    static = inspect_candidate(artifact)
    type_errors = typecheck_candidate(artifact)
    lint_errors = lint_candidate(artifact)
    dependency_errors = dependency_policy.validate(artifact.dependencies)
    unit = 0
    adversarial = 0
    variation = 0
    case_digests: list[str] = []
    all_runtime_passed = True
    if static.passed and not type_errors and not lint_errors and not dependency_errors:
        for case in cases:
            artifact.proposal.specification.validate_input(case.payload)
            result = sandbox.run(artifact, case.payload)
            passed = (
                result.succeeded
                and result.output == dict(case.expected)
                and artifact.proposal.specification.validate_output(result.output)
            )
            if not passed:
                all_runtime_passed = False
                continue
            case_digests.append(case.case_digest)
            if case.kind is ValidationKind.UNIT:
                unit += 1
            elif case.kind is ValidationKind.ADVERSARIAL:
                adversarial += 1
            else:
                variation += 1
    else:
        all_runtime_passed = False
    after = trusted_kernel_fingerprint()
    policy = sandbox.policy
    return ValidationEvidence(
        artifact_digest=artifact.digest,
        static_passed=static.passed,
        typecheck_passed=not type_errors,
        lint_passed=not lint_errors,
        dependency_passed=not dependency_errors,
        unit_successes=unit if all_runtime_passed else 0,
        adversarial_successes=adversarial if all_runtime_passed else 0,
        variation_successes=variation if all_runtime_passed else 0,
        case_digests=tuple(case_digests if all_runtime_passed else ()),
        sandboxed=True,
        network_isolated=not policy.network_allowed,
        filesystem_isolated=not policy.filesystem_allowed,
        resource_limited=(
            policy.timeout_seconds > 0
            and policy.max_input_bytes > 0
            and policy.max_output_bytes > 0
            and policy.max_steps > 0
        ),
        kernel_before=before,
        kernel_after=after,
        validated_at=validated_at,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanReviewPackage:
    package_id: UUID
    artifact_digest: str
    identity: CapabilityIdentity
    provenance: CandidateProvenance
    evidence: ValidationEvidence
    summary: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        artifact: CandidateArtifact,
        evidence: ValidationEvidence,
        created_at: datetime,
    ) -> HumanReviewPackage:
        if evidence.artifact_digest != artifact.digest or not evidence.passed:
            raise SelfExtensionSecurityError("only fully validated exact artifacts are reviewable")
        return cls(
            package_id=uuid4(),
            artifact_digest=artifact.digest,
            identity=artifact.proposal.identity,
            provenance=artifact.provenance,
            evidence=evidence,
            summary=(
                f"Validated {artifact.proposal.identity} from "
                f"{artifact.provenance.source_type.value}; {evidence.total_successes} varied "
                "sandbox successes; Trusted Kernel fingerprint unchanged."
            ),
            created_at=created_at,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.package_id, UUID):
            raise TypeError("package_id must be UUID")
        object.__setattr__(
            self,
            "artifact_digest",
            _validate_digest(self.artifact_digest, name="artifact_digest"),
        )
        if not isinstance(self.identity, CapabilityIdentity):
            raise TypeError("identity must be CapabilityIdentity")
        if not isinstance(self.provenance, CandidateProvenance):
            raise TypeError("provenance must be CandidateProvenance")
        if not isinstance(self.evidence, ValidationEvidence):
            raise TypeError("evidence must be ValidationEvidence")
        _text(self.summary, name="summary")
        object.__setattr__(self, "created_at", _aware(self.created_at, name="created_at"))

    def signing_payload(self) -> bytes:
        return _canonical_json(
            {
                "package_id": str(self.package_id),
                "artifact_digest": self.artifact_digest,
                "identity": _identity_dict(self.identity),
                "created_at": self.created_at.isoformat(),
                "successes": self.evidence.total_successes,
            }
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InstallationApproval:
    package_id: UUID
    artifact_digest: str
    reviewer: str
    approved_at: datetime
    authority_id: str
    mac: str

    def __post_init__(self) -> None:
        if not isinstance(self.package_id, UUID):
            raise TypeError("package_id must be UUID")
        object.__setattr__(
            self,
            "artifact_digest",
            _validate_digest(self.artifact_digest, name="artifact_digest"),
        )
        _text(self.reviewer, name="reviewer")
        object.__setattr__(self, "approved_at", _aware(self.approved_at, name="approved_at"))
        _text(self.authority_id, name="authority_id", maximum=128)
        _validate_digest(self.mac, name="approval.mac")


class TrustedApprovalAuthority:
    """Trusted host-side approval signer; the key is never exposed to candidates."""

    __slots__ = ("_authority_id", "_key")

    def __init__(self, *, authority_id: str, key: bytes) -> None:
        self._authority_id = _text(authority_id, name="authority_id", maximum=128)
        if not isinstance(key, bytes) or len(key) < 32:
            raise SelfExtensionSecurityError("approval key must contain at least 32 bytes")
        self._key = key

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def approve(
        self,
        package: HumanReviewPackage,
        *,
        reviewer: str,
        approved_at: datetime,
    ) -> InstallationApproval:
        reviewer = _text(reviewer, name="reviewer")
        approved_at = _aware(approved_at, name="approved_at")
        payload = _canonical_json(
            {
                "review": package.signing_payload().decode("utf-8"),
                "reviewer": reviewer,
                "approved_at": approved_at.isoformat(),
                "authority_id": self._authority_id,
            }
        )
        mac = _digest_bytes(hmac.new(self._key, payload, hashlib.sha256).digest())
        return InstallationApproval(
            package_id=package.package_id,
            artifact_digest=package.artifact_digest,
            reviewer=reviewer,
            approved_at=approved_at,
            authority_id=self._authority_id,
            mac=mac,
        )

    def verify(self, package: HumanReviewPackage, approval: InstallationApproval) -> bool:
        if (
            approval.package_id != package.package_id
            or approval.artifact_digest != package.artifact_digest
            or approval.authority_id != self._authority_id
        ):
            return False
        payload = _canonical_json(
            {
                "review": package.signing_payload().decode("utf-8"),
                "reviewer": approval.reviewer,
                "approved_at": approval.approved_at.isoformat(),
                "authority_id": self._authority_id,
            }
        )
        expected = _digest_bytes(hmac.new(self._key, payload, hashlib.sha256).digest())
        return hmac.compare_digest(expected, approval.mac)


class ExtensionHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    REVOKED = "revoked"


@dataclass(slots=True)
class _ExtensionRecord:
    artifact: CandidateArtifact
    evidence: ValidationEvidence
    review: HumanReviewPackage
    status: CandidateLifecycle
    health: ExtensionHealthState = ExtensionHealthState.HEALTHY
    consecutive_failures: int = 0
    successes: int = 0


@dataclass(frozen=True, slots=True)
class GeneratedCapabilityParams(CapabilityParams):
    payload_json: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> GeneratedCapabilityParams:
        encoded = _canonical_json(dict(payload))
        if len(encoded) > _MAX_PAYLOAD_BYTES:
            raise SelfExtensionError("payload exceeds limit")
        return cls(payload_json=encoded.decode("utf-8"))

    def __post_init__(self) -> None:
        if not isinstance(self.payload_json, str):
            raise TypeError("payload_json must be str")
        raw = self.payload_json.encode("utf-8")
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise SelfExtensionError("payload exceeds limit")
        decoded = json.loads(self.payload_json)
        if not isinstance(decoded, dict):
            raise SelfExtensionError("generated capability payload must be a JSON object")

    def payload(self) -> dict[str, object]:
        decoded = json.loads(self.payload_json)
        if not isinstance(decoded, dict):
            raise SelfExtensionError("generated capability payload must be a JSON object")
        return cast(dict[str, object], decoded)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"payload": cast(JsonValue, self.payload())}


class GeneratedCapability:
    """Canonical Capability wrapper; candidate source remains isolated data."""

    __slots__ = ("_artifact", "_descriptor", "_is_active", "_sandbox")

    def __init__(
        self,
        *,
        artifact: CandidateArtifact,
        sandbox: GeneratedToolSandbox,
        is_active: object,
    ) -> None:
        if not callable(is_active):
            raise TypeError("is_active must be callable")
        self._artifact = artifact
        self._sandbox = sandbox
        self._is_active = is_active
        self._descriptor = CapabilityDescriptor(
            identity=artifact.proposal.identity,
            description=artifact.proposal.description,
            scope=CapabilityScope(platform=artifact.proposal.platform),
            required_permissions=artifact.proposal.required_permissions,
            risk_assessment=artifact.proposal.risk_assessment,
            preconditions=(),
            rollback=RollbackDeclaration(
                support=RollbackSupport.NOT_APPLICABLE,
                detail=(
                    "Generated M15 tools are side-effect-free; external rollback "
                    "is not applicable."
                ),
            ),
            estimate=ResourceEstimate(
                wall_clock=timedelta(seconds=sandbox.policy.timeout_seconds),
                machine_actions=1,
                external_cost=Decimal("0"),
            ),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def _active(self) -> bool:
        callback = self._is_active
        return bool(callback(self._artifact.proposal.identity, self._artifact.digest))  # type: ignore[operator]

    def execute(
        self,
        request: CapabilityRequest[GeneratedCapabilityParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        del context
        if request.identity != self._artifact.proposal.identity:
            raise SelfExtensionError("generated capability request identity mismatch")
        if not isinstance(request.params, GeneratedCapabilityParams):
            raise TypeError("generated capability requires GeneratedCapabilityParams")
        if not self._active():
            return ExecutionResult(
                succeeded=False,
                message="generated capability is not active",
                observation=CapabilityObservation(
                    summary="generated capability activation guard denied execution",
                    data={"artifact_digest": self._artifact.digest, "active": False},
                ),
            )
        payload = request.params.payload()
        self._artifact.proposal.specification.validate_input(payload)
        result = self._sandbox.run(self._artifact, payload)
        if not result.succeeded:
            return ExecutionResult(
                succeeded=False,
                message="generated capability sandbox execution failed",
                observation=CapabilityObservation(
                    summary="generated capability sandbox failed",
                    data={
                        "artifact_digest": self._artifact.digest,
                        "error_code": result.error_code,
                    },
                ),
            )
        return ExecutionResult(
            succeeded=True,
            message="generated capability sandbox execution completed",
            observation=CapabilityObservation(
                summary="generated capability produced isolated output",
                data={
                    "artifact_digest": self._artifact.digest,
                    "output": result.output,
                    "transcript_digest": result.transcript_digest,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[GeneratedCapabilityParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        del context
        if not self._active():
            return VerificationResult(
                passed=False,
                detail="trusted activation guard reports this generated capability is inactive",
            )
        if observation.data.get("artifact_digest") != self._artifact.digest:
            return VerificationResult(
                passed=False,
                detail="observation is not bound to the validated artifact digest",
            )
        output = observation.data.get("output")
        payload = request.params.payload()
        expected_transcript = _digest_bytes(
            _canonical_json(
                {
                    "artifact_digest": self._artifact.digest,
                    "payload": payload,
                    "output": output,
                }
            )
        )
        if observation.data.get("transcript_digest") != expected_transcript:
            return VerificationResult(
                passed=False,
                detail="trusted sandbox transcript digest does not match execution evidence",
            )
        if not self._artifact.proposal.specification.validate_output(output):
            return VerificationResult(
                passed=False,
                detail="trusted verification contract rejected generated capability output",
            )
        return VerificationResult(
            passed=True,
            detail="trusted wrapper independently validated artifact identity and output contract",
        )


def _proposal_to_dict(proposal: CapabilityDesignProposal) -> dict[str, object]:
    risk = proposal.risk_assessment
    return {
        "proposal_id": str(proposal.proposal_id),
        "objective_id": str(proposal.objective_id),
        "identity": _identity_dict(proposal.identity),
        "description": proposal.description,
        "platform": proposal.platform.value,
        "required_permissions": sorted(item.value for item in proposal.required_permissions),
        "risk": {
            "level": risk.level.value,
            "reason": risk.reason,
            "reversible": risk.reversible,
            "external_effect": risk.external_effect,
            "read_only": risk.read_only,
            "modifies_state": risk.modifies_state,
            "critical": risk.critical,
            "destructive": risk.destructive,
        },
        "specification": {
            "input_keys": list(proposal.specification.input_keys),
            "output_fields": [
                {"name": item.name, "value_type": item.value_type.value}
                for item in proposal.specification.output_fields
            ],
        },
        "created_at": proposal.created_at.isoformat(),
    }


def _proposal_from_dict(raw: Mapping[str, object]) -> CapabilityDesignProposal:
    risk_raw = raw.get("risk")
    spec_raw = raw.get("specification")
    identity_raw = raw.get("identity")
    permissions_raw = raw.get("required_permissions")
    fields_raw = spec_raw.get("output_fields") if isinstance(spec_raw, Mapping) else None
    keys_raw = spec_raw.get("input_keys") if isinstance(spec_raw, Mapping) else None
    if (
        not isinstance(risk_raw, Mapping)
        or not isinstance(identity_raw, Mapping)
        or not isinstance(permissions_raw, list)
        or not isinstance(fields_raw, list)
        or not isinstance(keys_raw, list)
    ):
        raise SelfExtensionSecurityError("stored proposal is malformed")
    try:
        platform = CapabilityPlatform(cast(str, raw["platform"]))
        level = RiskLevel(cast(str, risk_raw["level"]))
        permissions = frozenset(Permission(cast(str, item)) for item in permissions_raw)
        output_fields = tuple(
            OutputField(
                name=cast(str, cast(Mapping[str, object], item)["name"]),
                value_type=OutputType(
                    cast(str, cast(Mapping[str, object], item)["value_type"])
                ),
            )
            for item in fields_raw
        )
        return CapabilityDesignProposal(
            proposal_id=UUID(cast(str, raw["proposal_id"])),
            objective_id=UUID(cast(str, raw["objective_id"])),
            identity=_identity_from_dict(identity_raw),
            description=cast(str, raw["description"]),
            platform=platform,
            required_permissions=permissions,
            risk_assessment=RiskAssessment(
                level=level,
                reason=cast(str, risk_raw["reason"]),
                reversible=cast(bool, risk_raw["reversible"]),
                external_effect=cast(bool, risk_raw["external_effect"]),
                read_only=cast(bool, risk_raw["read_only"]),
                modifies_state=cast(bool, risk_raw["modifies_state"]),
                critical=cast(bool, risk_raw["critical"]),
                destructive=cast(bool, risk_raw["destructive"]),
            ),
            specification=GeneratedToolSpecification(
                input_keys=tuple(cast(str, item) for item in keys_raw),
                output_fields=output_fields,
            ),
            created_at=datetime.fromisoformat(cast(str, raw["created_at"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SelfExtensionSecurityError("stored proposal is malformed") from exc


def _provenance_to_dict(value: CandidateProvenance) -> dict[str, object]:
    return {
        "source_type": value.source_type.value,
        "source": value.source,
        "retrieved_at": value.retrieved_at.isoformat(),
        "generator": value.generator,
        "parent_digest": value.parent_digest,
        "requesting_task": value.requesting_task,
    }


def _provenance_from_dict(raw: Mapping[str, object]) -> CandidateProvenance:
    try:
        return CandidateProvenance(
            source_type=CandidateSourceType(cast(str, raw["source_type"])),
            source=cast(str, raw["source"]),
            retrieved_at=datetime.fromisoformat(cast(str, raw["retrieved_at"])),
            generator=cast(str | None, raw.get("generator")),
            parent_digest=cast(str | None, raw.get("parent_digest")),
            requesting_task=cast(str | None, raw.get("requesting_task")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SelfExtensionSecurityError("stored provenance is malformed") from exc


class SelfExtensionManager:
    """Trusted M15 lifecycle owner with HMAC-protected restart persistence."""

    __slots__ = (
        "_approval_authority",
        "_integrity_key",
        "_path",
        "_records",
        "_registry",
        "_sandbox",
    )

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        sandbox: GeneratedToolSandbox,
        approval_authority: TrustedApprovalAuthority,
        integrity_key: bytes,
        persistence_path: Path | None = None,
    ) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be CapabilityRegistry")
        if not isinstance(sandbox, GeneratedToolSandbox):
            raise TypeError("sandbox must be GeneratedToolSandbox")
        if not isinstance(approval_authority, TrustedApprovalAuthority):
            raise TypeError("approval_authority must be TrustedApprovalAuthority")
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise SelfExtensionSecurityError("integrity key must contain at least 32 bytes")
        if persistence_path is not None and not isinstance(persistence_path, Path):
            raise TypeError("persistence_path must be Path or None")
        self._registry = registry
        self._sandbox = sandbox
        self._approval_authority = approval_authority
        self._integrity_key = integrity_key
        self._path = persistence_path
        self._records: dict[CapabilityIdentity, _ExtensionRecord] = {}
        if self._path is not None and self._path.exists():
            self._load()

    def is_active(self, identity: CapabilityIdentity, digest: str) -> bool:
        record = self._records.get(identity)
        return bool(
            record is not None
            and record.status is CandidateLifecycle.ACTIVE
            and record.health is not ExtensionHealthState.REVOKED
            and hmac.compare_digest(record.artifact.digest, digest)
        )

    def promote(
        self,
        *,
        artifact: CandidateArtifact,
        evidence: ValidationEvidence,
        review: HumanReviewPackage,
        approval: InstallationApproval,
    ) -> GeneratedCapability:
        if evidence.artifact_digest != artifact.digest or not evidence.passed:
            raise SelfExtensionSecurityError("promotion requires exact passing validation evidence")
        if (
            review.artifact_digest != artifact.digest
            or review.evidence.artifact_digest != artifact.digest
            or review.identity != artifact.proposal.identity
        ):
            raise SelfExtensionSecurityError("review package does not bind the exact artifact")
        if not self._approval_authority.verify(review, approval):
            raise SelfExtensionSecurityError("installation approval is missing or invalid")
        if trusted_kernel_fingerprint() != evidence.kernel_after:
            raise SelfExtensionSecurityError(
                "Trusted Kernel changed after candidate validation; revalidation is required"
            )
        current = self._records.get(artifact.proposal.identity)
        if current is not None and current.artifact.digest != artifact.digest:
            raise SelfExtensionError("exact capability version is already bound to different bytes")
        for identity, record in self._records.items():
            if (
                identity.name == artifact.proposal.identity.name
                and identity != artifact.proposal.identity
                and record.status is CandidateLifecycle.ACTIVE
            ):
                record.status = CandidateLifecycle.RETIRED
        record = _ExtensionRecord(
            artifact=artifact,
            evidence=evidence,
            review=review,
            status=CandidateLifecycle.ACTIVE,
        )
        self._records[artifact.proposal.identity] = record
        capability = GeneratedCapability(
            artifact=artifact,
            sandbox=self._sandbox,
            is_active=self.is_active,
        )
        try:
            self._registry.register(capability)
        except CapabilityAlreadyRegisteredError:
            existing = self._registry.describe(artifact.proposal.identity)
            if existing is None or existing.identity != artifact.proposal.identity:
                raise
        self._persist()
        return capability

    def record_verification(
        self,
        identity: CapabilityIdentity,
        *,
        passed: bool,
    ) -> ExtensionHealthState:
        if type(passed) is not bool:
            raise TypeError("passed must be bool")
        record = self._records.get(identity)
        if record is None:
            raise SelfExtensionError("generated capability is not managed")
        if passed:
            record.successes += 1
            record.consecutive_failures = 0
            if record.status is CandidateLifecycle.ACTIVE:
                record.health = ExtensionHealthState.HEALTHY
            self._persist()
            return record.health
        record.consecutive_failures += 1
        record.health = ExtensionHealthState.DEGRADED
        if record.consecutive_failures >= 2:
            record.health = ExtensionHealthState.REVOKED
            record.status = CandidateLifecycle.REVOKED
            self._rollback_latest(identity)
        self._persist()
        return record.health

    def revoke(self, identity: CapabilityIdentity) -> None:
        record = self._records.get(identity)
        if record is None:
            raise SelfExtensionError("generated capability is not managed")
        record.health = ExtensionHealthState.REVOKED
        record.status = CandidateLifecycle.REVOKED
        self._rollback_latest(identity)
        self._persist()

    def _rollback_latest(self, failed: CapabilityIdentity) -> None:
        candidates = [
            (identity, record)
            for identity, record in self._records.items()
            if identity.name == failed.name
            and identity != failed
            and record.status is CandidateLifecycle.RETIRED
            and record.health is not ExtensionHealthState.REVOKED
        ]
        if not candidates:
            return
        _identity, record = max(
            candidates,
            key=lambda item: (
                item[0].version.major,
                item[0].version.minor,
                item[0].version.patch,
            ),
        )
        record.status = CandidateLifecycle.ACTIVE
        record.health = ExtensionHealthState.HEALTHY

    def lifecycle(self, identity: CapabilityIdentity) -> CandidateLifecycle | None:
        record = self._records.get(identity)
        return None if record is None else record.status

    def health(self, identity: CapabilityIdentity) -> ExtensionHealthState | None:
        record = self._records.get(identity)
        return None if record is None else record.health

    def _stored_records(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for identity, record in sorted(
            self._records.items(),
            key=lambda item: (
                item[0].name.value,
                item[0].version.major,
                item[0].version.minor,
                item[0].version.patch,
            ),
        ):
            rows.append(
                {
                    "identity": _identity_dict(identity),
                    "proposal": _proposal_to_dict(record.artifact.proposal),
                    "source": record.artifact.source,
                    "provenance": _provenance_to_dict(record.artifact.provenance),
                    "dependencies": [
                        {
                            "name": item.name,
                            "version": item.version,
                            "digest": item.digest,
                        }
                        for item in record.artifact.dependencies
                    ],
                    "artifact_digest": record.artifact.digest,
                    "status": record.status.value,
                    "health": record.health.value,
                    "consecutive_failures": record.consecutive_failures,
                    "successes": record.successes,
                    "validated_at": record.evidence.validated_at.isoformat(),
                    "case_digests": list(record.evidence.case_digests),
                    "validation_successes": record.evidence.total_successes,
                }
            )
        return rows

    def _persist(self) -> None:
        if self._path is None:
            return
        records = self._stored_records()
        signed = _canonical_json({"schema": _SCHEMA_VERSION, "records": records})
        mac = _digest_bytes(hmac.new(self._integrity_key, signed, hashlib.sha256).digest())
        payload = _canonical_json(
            {"schema": _SCHEMA_VERSION, "records": records, "mac": mac}
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(self._path)

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SelfExtensionSecurityError("extension persistence is unreadable") from exc
        if not isinstance(raw, Mapping):
            raise SelfExtensionSecurityError("extension persistence root is malformed")
        schema = raw.get("schema")
        records_raw = raw.get("records")
        mac = raw.get("mac")
        if (
            schema != _SCHEMA_VERSION
            or not isinstance(records_raw, list)
            or not isinstance(mac, str)
        ):
            raise SelfExtensionSecurityError("extension persistence schema is malformed")
        signed = _canonical_json({"schema": schema, "records": records_raw})
        expected = _digest_bytes(hmac.new(self._integrity_key, signed, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, mac):
            raise SelfExtensionSecurityError("extension persistence integrity check failed")
        for item in records_raw:
            if not isinstance(item, Mapping):
                raise SelfExtensionSecurityError("stored extension record is malformed")
            proposal_raw = item.get("proposal")
            provenance_raw = item.get("provenance")
            dependencies_raw = item.get("dependencies")
            if (
                not isinstance(proposal_raw, Mapping)
                or not isinstance(provenance_raw, Mapping)
                or not isinstance(dependencies_raw, list)
            ):
                raise SelfExtensionSecurityError("stored extension record is malformed")
            proposal = _proposal_from_dict(proposal_raw)
            provenance = _provenance_from_dict(provenance_raw)
            dependencies = tuple(
                GeneratedDependency(
                    name=cast(str, cast(Mapping[str, object], dep)["name"]),
                    version=cast(str, cast(Mapping[str, object], dep)["version"]),
                    digest=cast(str, cast(Mapping[str, object], dep)["digest"]),
                )
                for dep in dependencies_raw
            )
            source = item.get("source")
            artifact_digest = item.get("artifact_digest")
            if not isinstance(source, str) or not isinstance(artifact_digest, str):
                raise SelfExtensionSecurityError("stored extension artifact is malformed")
            artifact = CandidateArtifact.create(
                proposal=proposal,
                source=source,
                provenance=provenance,
                dependencies=dependencies,
            )
            if not hmac.compare_digest(artifact.digest, artifact_digest):
                raise SelfExtensionSecurityError("stored artifact bytes changed after validation")
            status = CandidateLifecycle(cast(str, item["status"]))
            health = ExtensionHealthState(cast(str, item["health"]))
            successes_raw = item.get("validation_successes")
            failures_raw = item.get("consecutive_failures")
            observed_successes_raw = item.get("successes")
            case_digests_raw = item.get("case_digests")
            validated_at_raw = item.get("validated_at")
            if (
                type(successes_raw) is not int
                or type(failures_raw) is not int
                or type(observed_successes_raw) is not int
                or not isinstance(case_digests_raw, list)
                or not isinstance(validated_at_raw, str)
            ):
                raise SelfExtensionSecurityError("stored validation evidence is malformed")
            kernel = trusted_kernel_fingerprint()
            evidence = ValidationEvidence(
                artifact_digest=artifact.digest,
                static_passed=True,
                typecheck_passed=True,
                lint_passed=True,
                dependency_passed=True,
                unit_successes=successes_raw,
                adversarial_successes=0,
                variation_successes=0,
                case_digests=tuple(cast(str, value) for value in case_digests_raw),
                sandboxed=True,
                network_isolated=True,
                filesystem_isolated=True,
                resource_limited=True,
                kernel_before=kernel,
                kernel_after=kernel,
                validated_at=datetime.fromisoformat(validated_at_raw),
            )
            review = HumanReviewPackage(
                package_id=uuid4(),
                artifact_digest=artifact.digest,
                identity=artifact.proposal.identity,
                provenance=artifact.provenance,
                evidence=evidence,
                summary="Restored HMAC-protected M15 validation record.",
                created_at=evidence.validated_at,
            )
            record = _ExtensionRecord(
                artifact=artifact,
                evidence=evidence,
                review=review,
                status=status,
                health=health,
                consecutive_failures=failures_raw,
                successes=observed_successes_raw,
            )
            self._records[proposal.identity] = record
            if status is CandidateLifecycle.ACTIVE and health is not ExtensionHealthState.REVOKED:
                capability = GeneratedCapability(
                    artifact=artifact,
                    sandbox=self._sandbox,
                    is_active=self.is_active,
                )
                self._registry.register(capability)


def pure_read_risk() -> RiskAssessment:
    """Canonical R0 risk helper for side-effect-free generated tools."""
    return assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=False,
    )
