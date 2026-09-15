"""Typed, code-connected AgentX threat-model foundation (AX-390).

This module is descriptive security data, not runtime enforcement.  AX-040 owns
the Trusted-Kernel whole-system security audit and unsafe-action controls.  The
model here keeps the ongoing threat inventory code-connected: every control
names a boundary, protected assets, threat classes, an invariant, concrete
implementation paths, machine-test paths and residual risk.
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
    """One descriptive invariant connected to current production and tests."""

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
        if not isinstance(self.control_id, str):
            raise TypeError("control_id must be a string")
        if not self.control_id or self.control_id != self.control_id.strip():
            raise ValueError("control_id must be non-empty and trimmed")
        if not isinstance(self.boundary, ThreatBoundary):
            raise TypeError("boundary must be a ThreatBoundary")
        if not isinstance(self.disposition, TrustDisposition):
            raise TypeError("disposition must be a TrustDisposition")
        if not isinstance(self.assets, frozenset) or not self.assets:
            raise ValueError("assets must be a non-empty frozenset")
        if any(not isinstance(item, AgentXAsset) for item in self.assets):
            raise TypeError("assets must contain only AgentXAsset values")
        if not isinstance(self.threats, frozenset) or not self.threats:
            raise ValueError("threats must be a non-empty frozenset")
        if any(not isinstance(item, ThreatClass) for item in self.threats):
            raise TypeError("threats must contain only ThreatClass values")
        for field_name, values in (
            ("implementation_paths", self.implementation_paths),
            ("test_paths", self.test_paths),
        ):
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{field_name} must be a non-empty tuple")
            for value in values:
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{field_name} entries must be non-empty strings")
                path = PurePosixPath(value)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError(f"{field_name} entries must be repository-relative")
        if not isinstance(self.invariant, str) or not self.invariant.strip():
            raise ValueError("invariant must be non-empty")
        if not isinstance(self.residual_risk, str) or not self.residual_risk.strip():
            raise ValueError("residual_risk must be non-empty")


@dataclass(frozen=True, slots=True)
class ThreatModel:
    """Bounded immutable threat/control map for the current architecture."""

    controls: tuple[ThreatControl, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.controls, tuple) or not self.controls:
            raise ValueError("controls must be a non-empty tuple")
        if any(not isinstance(item, ThreatControl) for item in self.controls):
            raise TypeError("controls must contain only ThreatControl values")
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
    """Return the canonical bounded threat/control map for this repository tree."""
    controls = (
        _control(
            "kernel-authority",
            ThreatBoundary.TRUSTED_KERNEL,
            TrustDisposition.AUTHORITY_SOURCE,
            frozenset({AgentXAsset.AUTHORITY, AgentXAsset.RESOURCE_BUDGET}),
            frozenset(
                {ThreatClass.AUTHORITY_SPOOFING, ThreatClass.DESTRUCTIVE_ACTION}
            ),
            "Only canonical kernel policy may authorize governed machine actions.",
            (
                "src/agentx/kernel/action_gate.py",
                "src/agentx/kernel/permissions.py",
                "src/agentx/kernel/resource_budget.py",
            ),
            (
                "tests/unit/test_action_gate.py",
                "tests/adversarial/test_trusted_kernel_adversarial.py",
            ),
            "Correct policy cannot eliminate user regret or every host race.",
        ),
        _control(
            "model-data",
            ThreatBoundary.MODEL_PROVIDER,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.TASK_STATE, AgentXAsset.AUTHORITY}),
            frozenset(
                {
                    ThreatClass.PROMPT_INJECTION,
                    ThreatClass.AUTHORITY_SPOOFING,
                    ThreatClass.PROVIDER_FAILURE,
                }
            ),
            "Model output is data and cannot directly create authority or success.",
            (
                "src/agentx/cognition/model_provider.py",
                "src/agentx/cognition/reasoner.py",
            ),
            ("tests/unit/test_model_provider.py", "tests/unit/test_reasoner.py"),
            "Incorrect model content can still mislead later deterministic consumers.",
        ),
        _control(
            "research-data",
            ThreatBoundary.RESEARCH,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.KNOWLEDGE, AgentXAsset.AUTHORITY}),
            frozenset({ThreatClass.PROMPT_INJECTION, ThreatClass.PROVIDER_FAILURE}),
            "Research evidence remains untrusted until independently revalidated.",
            (
                "src/agentx/cognition/research_provider.py",
                "src/agentx/cognition/research_acquisition.py",
            ),
            (
                "tests/adversarial/test_research_provider_authority.py",
                "tests/adversarial/test_research_acquisition_authority.py",
            ),
            "External provenance can be forged or become stale after retrieval.",
        ),
        _control(
            "browser-boundary",
            ThreatBoundary.BROWSER,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.AUTHORITY}),
            frozenset(
                {
                    ThreatClass.PROMPT_INJECTION,
                    ThreatClass.STALE_EVIDENCE,
                    ThreatClass.TOCTOU,
                }
            ),
            "DOM data is untrusted and browser mutations remain governed actions.",
            (
                "src/agentx/capabilities/browser_dom.py",
                "src/agentx/capabilities/browser_actions.py",
            ),
            (
                "tests/adversarial/test_browser_dom_authority.py",
                "tests/adversarial/test_browser_actions_adversarial.py",
            ),
            "Pages may change between observation, target resolution and action.",
        ),
        _control(
            "windows-ui-boundary",
            ThreatBoundary.WINDOWS_UI,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.TOCTOU, ThreatClass.STALE_EVIDENCE}),
            "Native dispatch never proves the intended user-visible state transition.",
            (
                "src/agentx/capabilities/windows/window_management_v2.py",
                "src/agentx/windows_transition_verification.py",
            ),
            (
                "tests/adversarial/test_windows_window_management_v2_authority.py",
                "tests/adversarial/test_windows_transition_verification_authority.py",
            ),
            "Applications can expose incomplete or rapidly changing UI state.",
        ),
        _control(
            "clipboard-boundary",
            ThreatBoundary.CLIPBOARD,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.USER_DATA, AgentXAsset.SECRETS}),
            frozenset(
                {ThreatClass.DATA_EXFILTRATION, ThreatClass.PROMPT_INJECTION}
            ),
            "Clipboard text is untrusted and must not become authority or unsafe telemetry.",
            ("src/agentx/capabilities/windows/keyboard_text_clipboard.py",),
            ("tests/adversarial/test_windows_keyboard_text_clipboard_authority.py",),
            "Other applications can mutate clipboard state concurrently.",
        ),
        _control(
            "filesystem-boundary",
            ThreatBoundary.FILESYSTEM,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset({AgentXAsset.USER_DATA}),
            frozenset(
                {
                    ThreatClass.DESTRUCTIVE_ACTION,
                    ThreatClass.TOCTOU,
                    ThreatClass.DATA_EXFILTRATION,
                }
            ),
            "Filesystem mutation is governed and API return is not verification.",
            (
                "src/agentx/capabilities/filesystem.py",
                "src/agentx/capabilities/filesystem_structural_risk.py",
            ),
            (
                "tests/adversarial/test_filesystem_capability_adversarial.py",
                "tests/unit/test_filesystem_structural_risk.py",
            ),
            "Host filesystem state can race after any preflight observation.",
        ),
        _control(
            "memory-boundary",
            ThreatBoundary.MEMORY,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.KNOWLEDGE, AgentXAsset.AUTHORITY}),
            frozenset(
                {
                    ThreatClass.AUTHORITY_SPOOFING,
                    ThreatClass.STALE_EVIDENCE,
                    ThreatClass.UNSAFE_REPLAY,
                }
            ),
            "Knowledge status, provenance and content remain data rather than authority.",
            (
                "src/agentx/infrastructure/knowledge_store.py",
                "src/agentx/infrastructure/knowledge_retrieval.py",
            ),
            (
                "tests/unit/test_knowledge_integrity.py",
                "tests/architecture/test_knowledge_retrieval_boundaries.py",
            ),
            "Persisted claims can remain wrong even when provenance is preserved.",
        ),
        _control(
            "persistence-boundary",
            ThreatBoundary.PERSISTENCE,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset(
                {AgentXAsset.KNOWLEDGE, AgentXAsset.PROCEDURES, AgentXAsset.EVIDENCE}
            ),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.STALE_EVIDENCE}),
            "Durable state is schema-validated and corrupt records fail closed.",
            (
                "src/agentx/infrastructure/persistence.py",
                "src/agentx/infrastructure/recovery.py",
            ),
            (
                "tests/unit/test_persistence.py",
                "tests/unit/test_persistence_recovery.py",
            ),
            "Storage corruption can make valid state unavailable until operator recovery.",
        ),
        _control(
            "procedure-compiler",
            ThreatBoundary.PROCEDURE_COMPILER,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.PROCEDURES}),
            frozenset({ThreatClass.UNSAFE_REPLAY, ThreatClass.PROMPT_INJECTION}),
            "Generated procedures require independent validation and explicit promotion.",
            (
                "src/agentx/procedure_validation.py",
                "src/agentx/procedure_promotion.py",
            ),
            (
                "tests/unit/test_procedure_validation_policy.py",
                "tests/unit/test_procedure_promotion.py",
            ),
            "Validation cannot prove correctness under every future environment change.",
        ),
        _control(
            "procedure-runtime",
            ThreatBoundary.PROCEDURE_RUNTIME,
            TrustDisposition.TRUSTED_ENFORCEMENT,
            frozenset({AgentXAsset.PROCEDURES, AgentXAsset.TASK_STATE}),
            frozenset(
                {ThreatClass.UNSAFE_REPLAY, ThreatClass.RESOURCE_EXHAUSTION}
            ),
            "Procedure execution is bounded and END is not task success.",
            (
                "src/agentx/compiled_procedure_strategy.py",
                "src/agentx/guided_procedure_strategy.py",
            ),
            (
                "tests/integration/test_compiled_procedure_strategy.py",
                "tests/integration/test_guided_procedure_strategy.py",
            ),
            "An ACTIVE procedure may become stale as environmental assumptions drift.",
        ),
        _control(
            "repair-boundary",
            ThreatBoundary.REPAIR,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.PROCEDURES, AgentXAsset.TASK_STATE}),
            frozenset(
                {ThreatClass.UNSAFE_REPLAY, ThreatClass.AUTHORITY_SPOOFING}
            ),
            "Repair evidence cannot activate a replacement or grant execution authority.",
            (
                "src/agentx/repair_workflow.py",
                "src/agentx/core/repair_validation.py",
            ),
            (
                "tests/adversarial/test_repair_workflow_authority.py",
                "tests/unit/test_repair_validation.py",
            ),
            "Automated repair validation can miss environment-specific failures.",
        ),
        _control(
            "world-state",
            ThreatBoundary.WORLD_STATE,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.TASK_STATE, AgentXAsset.USER_DATA}),
            frozenset({ThreatClass.STALE_EVIDENCE, ThreatClass.TOCTOU}),
            "World-state observations carry freshness and never become authority.",
            ("src/agentx/core/world_state.py",),
            ("tests/adversarial/test_world_state_authority.py",),
            "Observed state can become stale immediately after capture.",
        ),
        _control(
            "event-system",
            ThreatBoundary.EVENT_SYSTEM,
            TrustDisposition.EVIDENCE_ONLY,
            frozenset({AgentXAsset.EVIDENCE, AgentXAsset.TASK_STATE}),
            frozenset({ThreatClass.EVENT_SPOOFING, ThreatClass.UNSAFE_REPLAY}),
            "Events are typed evidence; publication is not permission or verification.",
            ("src/agentx/core/events.py", "src/agentx/infrastructure/event_bus.py"),
            ("tests/unit/test_events.py", "tests/unit/test_event_bus.py"),
            "A valid event can still describe stale or malicious external data.",
        ),
        _control(
            "extension-boundary",
            ThreatBoundary.EXTENSIONS,
            TrustDisposition.UNTRUSTED_DATA,
            frozenset({AgentXAsset.AUTHORITY, AgentXAsset.USER_DATA}),
            frozenset({ThreatClass.SUPPLY_CHAIN, ThreatClass.AUTHORITY_SPOOFING}),
            "Capability discovery identifies implementations but never grants authority.",
            ("src/agentx/capabilities/registry.py",),
            (
                "tests/unit/test_capability_registry.py",
                "tests/architecture/test_capability_registry_placement.py",
            ),
            "Future dynamic extension loading will require additional supply-chain controls.",
        ),
    )
    return ThreatModel(controls=controls)
