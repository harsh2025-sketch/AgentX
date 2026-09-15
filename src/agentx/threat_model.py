"""Typed, code-connected AgentX threat-model foundation (AX-390).

This module is descriptive security data, not enforcement. Runtime unsafe-action
detection remains owned by AX-040 and is deliberately not reimplemented here.
The model names trust boundaries, assets, abuse classes, invariant controls and
the machine-test files that exercise those controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

__all__ = [
    "AgentXAsset",
    "ThreatBoundary",
    "ThreatClass",
    "ThreatControl",
    "ThreatModel",
    "TrustDisposition",
    "canonical_threat_model",
]


class AgentXAsset(StrEnum):
    AUTHORITY = "authority"
    SECRETS = "secrets"
    USER_DATA = "user_data"
    TASK_STATE = "task_state"
    KNOWLEDGE = "knowledge"
    PROCEDURES = "procedures"
    EVIDENCE = "evidence"
    RESOURCE_BUDGET = "resource_budget"


class ThreatBoundary(StrEnum):
    TRUSTED_KERNEL = "trusted_kernel"
    MODEL_PROVIDER = "model_provider"
    RESEARCH = "research"
    BROWSER = "browser"
    WINDOWS_UI = "windows_ui"
    CLIPBOARD = "clipboard"
    FILESYSTEM = "filesystem"
    MEMORY = "memory"
    PERSISTENCE = "persistence"
    PROCEDURE_COMPILER = "procedure_compiler"
    PROCEDURE_RUNTIME = "procedure_runtime"
    REPAIR = "repair"
    WORLD_STATE = "world_state"
    EVENT_SYSTEM = "event_system"
    EXTENSIONS = "extensions"


class TrustDisposition(StrEnum):
    AUTHORITY_SOURCE = "authority_source"
    TRUSTED_ENFORCEMENT = "trusted_enforcement"
    UNTRUSTED_DATA = "untrusted_data"
    EVIDENCE_ONLY = "evidence_only"


class ThreatClass(StrEnum):
    AUTHORITY_SPOOFING = "authority_spoofing"
    PROMPT_INJECTION = "prompt_injection"
    STALE_EVIDENCE = "stale_evidence"
    TOCTOU = "toctou"
    UNSAFE_REPLAY = "unsafe_replay"
    DATA_EXFILTRATION = "data_exfiltration"
    DESTRUCTIVE_ACTION = "destructive_action"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    EVENT_SPOOFING = "event_spoofing"
    PROVIDER_FAILURE = "provider_failure"
    SUPPLY_CHAIN = "supply_chain"


@dataclass(frozen=True, slots=True)
class ThreatControl:
    control_id: str
    boundary: ThreatBoundary
    disposition: TrustDisposition
    assets: frozenset[AgentXAsset]
    threats: frozenset[ThreatClass]
    invariant: str
    implementation_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    residual_risk: str

    def __post_init__(self) -> None:
        if not self.control_id or self.control_id != self.control_id.strip():
            raise ValueError("control_id must be non-empty and trimmed")
        if not isinstance(self.boundary, ThreatBoundary):
            raise TypeError("boundary must be a ThreatBoundary")
        if not isinstance(self.disposition, TrustDisposition):
            raise TypeError("disposition must be a TrustDisposition")
        if not self.assets or any(not isinstance(item, AgentXAsset) for item in self.assets):
            raise ValueError("assets must be a non-empty AgentXAsset set")
        if not self.threats or any(not isinstance(item, ThreatClass) for item in self.threats):
            raise ValueError("threats must be a non-empty ThreatClass set")
        for field_name, values in (
            ("implementation_paths", self.implementation_paths),
            ("test_paths", self.test_paths),
        ):
            if not values:
                raise ValueError(f"{field_name} must not be empty")
            for value in values:
                if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
                    raise ValueError(f"{field_name} entries must be repository-relative")
        if not self.invariant.strip() or not self.residual_risk.strip():
            raise ValueError("invariant and residual_risk must be non-empty")


@dataclass(frozen=True, slots=True)
class ThreatModel:
    controls: tuple[ThreatControl, ...]

    def __post_init__(self) -> None:
        if not self.controls:
            raise ValueError("controls must not be empty")
        ids = tuple(item.control_id for item in self.controls)
        if len(ids) != len(set(ids)):
            raise ValueError("control_id values must be unique")

    @property
    def covered_boundaries(self) -> frozenset[ThreatBoundary]:
        return frozenset(item.boundary for item in self.controls)

    @property
    def covered_threats(self) -> frozenset[ThreatClass]:
        return frozenset(threat for item in self.controls for threat in item.threats)


def _control(
    control_id: str,
    boundary: ThreatBoundary,
    disposition: TrustDisposition,
    assets: frozenset[AgentXAsset],
    threats: frozenset[ThreatClass],
    invariant: str,
    implementation_paths: tuple[str, ...],
    test_paths: tuple[str, ...],
    residual_risk: str,
) -> ThreatControl:
    return ThreatControl(
        control_id=control_id,
        boundary=boundary,
        disposition=disposition,
        assets=assets,
        threats=threats,
        invariant=invariant,
        implementation_paths=implementation_paths,
        test_paths=test_paths,
        residual_risk=residual_risk,
    )


def canonical_threat_model() -> ThreatModel:
    """Return the bounded canonical threat-control map for the current tree."""
    controls = (
        _control(
            "kernel-authority",
            ThreatBoundary.TRUSTED_KERNEL,
            TrustDisposition.AUTHORITY_SOURCE,
            frozenset({AgentXAsset.AUTHORITY, AgentXAsset.RESOURCE_BUDGET}),
            frozenset({ThreatClass.AUTHORITY_SPOOFING, ThreatClass.DESTRUCTIVE_ACTION}),
            "Only canonical authority, permission, risk, ActionGate and budget contracts may authorize machine actions.",
            ("src/agentx/kernel/action_gate.py", "src/agentx/kernel/permissions.py", "src/agentx/kernel/resource_budget.py"),
            ("tests/unit/test_action_gate.py", "tests/adversarial/test_action_gate_adversarial.py"),
            "A correct policy can still authorize an operation the user later regrets; approval semantics remain operation-specific.",
        ),
        _control(
            "model-data",
            ThreatBoundary.MODEL_PROVIDER,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.TASK_STATE, AgentXAsset.AUTHORITY}),
            frozenset({ThreatClass.PROMPT_INJECTION, ThreatClass.AUTHORITY_SPOOFING, ThreatClass.PROVIDER_FAILURE}),
            "Model output is data and cannot directly mutate authority, verification truth, or task success.",
            ("src/agentx/cognition/model_provider.py", "src/agentx/cognition/reasoner.py"),
            ("tests/adversarial/test_model_provider_adversarial.py", "tests/adversarial/test_reasoner_adversarial.py"),
            "Models may still produce incorrect content that later deterministic code must validate before use.",
        ),
        _control(
            "research-data",
            ThreatBoundary.RESEARCH,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.KNOWLEDGE, AgentXAsset.AUTHORITY}),
            frozenset({ThreatClass.PROMPT_INJECTION, ThreatClass.PROVIDER_FAILURE}),
            "Research-provider evidence is untrusted and cannot itself create verified knowledge or authority.",
            ("src/agentx/cognition/research_provider.py", "src/agentx/cognition/research_acquisition.py"),
            ("tests/adversarial/test_research_provider_authority.py", "tests/adversarial/test_research_acquisition_authority.py"),
            "Source provenance can be forged externally; later revalidation is required before load-bearing truth use.",
        ),
        _control(
            "browser-boundary",
            ThreatBoundary.BROWSER,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.AUTHORITY}),
            frozenset({ThreatClass.PROMPT_INJECTION, ThreatClass.STALE_EVIDENCE}),
            "DOM/page content is untrusted observation and browser actions remain governed capabilities.",
            ("src/agentx/capabilities/browser_dom.py", "src/agentx/capabilities/browser_actions.py"),
            ("tests/adversarial/test_browser_actions.py", "tests/unit/test_browser_dom.py"),
            "A page can change between observation and action; targeting must revalidate freshness and identity.",
        ),
        _control(
            "windows-ui-boundary",
            ThreatBoundary.WINDOWS_UI,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.TOCTOU, ThreatClass.STALE_EVIDENCE}),
            "Windows UI observations never equal successful state transition; execution and verification are separate.",
            ("src/agentx/capabilities/windows/",),
            ("tests/adversarial/test_windows_capabilities.py",),
            "Applications may expose incomplete accessibility state; some actions can remain observed-only.",
        ),
        _control(
            "clipboard-boundary",
            ThreatBoundary.CLIPBOARD,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.SECRETS}),
            frozenset({ThreatClass.DATA_EXFILTRATION, ThreatClass.PROMPT_INJECTION}),
            "Clipboard text is untrusted and must not become authority or be logged without need.",
            ("src/agentx/capabilities/windows/clipboard.py",),
            ("tests/adversarial/test_windows_clipboard.py",),
            "Other applications can mutate the clipboard concurrently between write and verification.",
        ),
        _control(
            "filesystem-boundary",
            ThreatBoundary.FILESYSTEM,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset({AgentXAsset.USER_DATA}),
            frozenset({ThreatClass.DESTRUCTIVE_ACTION, ThreatClass.TOCTOU, ThreatClass.DATA_EXFILTRATION}),
            "Filesystem mutation is permission/risk gated and independently verified; path text is inert.",
            ("src/agentx/capabilities/filesystem.py", "src/agentx/capabilities/filesystem_structural_risk.py"),
            ("tests/adversarial/test_filesystem_capability.py", "tests/unit/test_filesystem_structural_risk.py"),
            "Filesystem state can race after preflight; destructive operations need conservative request-sensitive policy.",
        ),
        _control(
            "memory-boundary",
            ThreatBoundary.MEMORY,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.KNOWLEDGE, AgentXAsset.AUTHORITY}),
            frozenset({ThreatClass.AUTHORITY_SPOOFING, ThreatClass.STALE_EVIDENCE, ThreatClass.UNSAFE_REPLAY}),
            "Knowledge lifecycle/status/provenance remain data and never grant machine authority.",
            ("src/agentx/infrastructure/knowledge_store.py", "src/agentx/infrastructure/knowledge_retrieval.py"),
            ("tests/unit/test_knowledge_integrity.py", "tests/architecture/test_knowledge_retrieval_boundaries.py"),
            "Stored claims may remain wrong despite provenance; contradiction and revalidation policy must surface uncertainty.",
        ),
        _control(
            "persistence-boundary",
            ThreatBoundary.PERSISTENCE,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset({AgentXAsset.KNOWLEDGE, AgentXAsset.PROCEDURES, AgentXAsset.EVIDENCE}),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.STALE_EVIDENCE}),
            "Durable rows are schema-validated and corruption fails closed instead of being repaired silently.",
            ("src/agentx/infrastructure/persistence.py", "src/agentx/infrastructure/recovery.py"),
            ("tests/unit/test_persistence.py", "tests/unit/test_recovery.py"),
            "SQLite/file-system failure can make state unavailable; operator recovery is still required for some corruption.",
        ),
        _control(
            "procedure-compiler",
            ThreatBoundary.PROCEDURE_COMPILER,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.PROCEDURES}),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.PROMPT_INJECTION}),
            "Generated candidates require independent validation and explicit promotion before ACTIVE reuse.",
            ("src/agentx/procedure_validation.py", "src/agentx/procedure_promotion.py"),
            ("tests/unit/test_procedure_validation_policy.py", "tests/unit/test_procedure_promotion.py"),
            "Validation environments can miss future drift; ACTIVE lifecycle is not permanent correctness.",
        ),
        _control(
            "procedure-runtime",
            ThreatBoundary.PROCEDURE_RUNTIME,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset({AgentXAsset.PROCEDURES, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.RESOURCE_EXHAUSTION}),
            "Procedure execution is bounded, uses governed capabilities, and END is not task success.",
            ("src/agentx/compiled_procedure_strategy.py", "src/agentx/guided_procedure_strategy.py"),
            ("tests/integration/test_compiled_procedure_strategy.py", "tests/integration/test_guided_procedure_strategy.py"),
            "An ACTIVE procedure can become stale when environment assumptions change and must be invalidated by higher layers.",
        ),
        _control(
            "repair-boundary",
            ThreatBoundary.REPAIR,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.PROCEDURES, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.AUTHORITY_SPOOFING}),
            "Repair proposals and validation evidence cannot activate replacements without canonical lifecycle policy.",
            ("src/agentx/repair_workflow.py", "src/agentx/core/repair_validation.py"),
            ("tests/adversarial/test_repair_workflow_authority.py", "tests/unit/test_repair_validation.py"),
            "Automated validation can be incomplete; destructive repair still needs governed execution.",
        ),
        _control(
            "world-state",
            ThreatBoundary.WORLD_STATE,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.TASK_STATE, AgentXAsset.USER_DATA}),
            frozenset({ThreatClass.STALE_EVIDENCE, ThreatClass.TOCTOU}),
            "World observations are timestamped evidence, never current truth without freshness evidence.",
            ("src/agentx/world/",),
            ("tests/unit/test_world_state.py",),
            "Providers can be incomplete or delayed; absence of observation is not proof of absence.",
        ),
        _control(
            "events-boundary",
            ThreatBoundary.EVENT_SYSTEM,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.EVIDENCE, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.EVENT_SPOOFING, ThreatClass.RESOURCE_EXHAUSTION}),
            "Events describe runtime facts but never grant authority or mutate canonical task state by themselves.",
            ("src/agentx/core/events.py", "src/agentx/infrastructure/event_bus.py"),
            ("tests/unit/test_events.py", "tests/unit/test_event_bus.py"),
            "Consumers can disconnect or lag; bounded queues and replay semantics are needed for UI/watchers.",
        ),
        _control(
            "extensions-boundary",
            ThreatBoundary.EXTENSIONS,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.AUTHORITY, AgentXAsset.SECRETS, AgentXAsset.USER_DATA}),
            frozenset({ThreatClass.SUPPLY_CHAIN, ThreatClass.AUTHORITY_SPOOFING}),
            "Future extension metadata cannot create permissions or bypass canonical registry/governance boundaries.",
            ("src/agentx/capabilities/registry.py",),
            ("tests/architecture/test_capability_placement.py",),
            "Future plugin execution increases supply-chain attack surface and requires an explicit governed loading design.",
        ),
    )
    return ThreatModel(controls=controls)
