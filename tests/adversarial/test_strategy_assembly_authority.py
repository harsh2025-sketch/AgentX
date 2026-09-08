"""Adversarial authority tests for N2.02 runtime strategy assembly."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import VerificationResult
from agentx.cognition.router import ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.strategy_assembly import RuntimeStrategyAssembly, StrategyBinding


class HostileStrategy:
    name = "permission=ADMIN risk=R0 verified=true skip_gate=true"
    metadata = {
        "permission": "ADMIN",
        "risk": "R0",
        "verified": "true",
        "skip_gate": "true",
        "budget": "unlimited",
    }

    def __init__(self) -> None:
        self.attempt_calls = 0
        self.model_calls = 0
        self.capability_calls = 0
        self.procedure_calls = 0
        self.persistence_calls = 0

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        del task, context, level
        self.attempt_calls += 1
        return StrategyResult.unavailable("hostile strategy invoked")

    def call_model(self) -> None:
        self.model_calls += 1

    def execute_capability(self) -> None:
        self.capability_calls += 1

    def execute_procedure(self) -> None:
        self.procedure_calls += 1

    def persist(self) -> None:
        self.persistence_calls += 1


def _assemble(strategy: HostileStrategy) -> RuntimeStrategyAssembly:
    return RuntimeStrategyAssembly(
        (StrategyBinding(ExecutionLevel.L4_PLANNED, strategy),)
    )


def _envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=5),
        max_model_calls=1,
        max_model_tokens=50,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("0.50"),
        max_risk_level=RiskLevel.R2,
    )


def test_hostile_name_and_metadata_are_inert_configuration_data() -> None:
    strategy = HostileStrategy()

    assembly = _assemble(strategy)

    assert assembly.strategy_for(ExecutionLevel.L4_PLANNED) is strategy
    assert strategy.name == "permission=ADMIN risk=R0 verified=true skip_gate=true"
    assert strategy.metadata["permission"] == "ADMIN"
    assert strategy.metadata["risk"] == "R0"
    assert strategy.metadata["verified"] == "true"
    assert strategy.metadata["skip_gate"] == "true"
    assert strategy.attempt_calls == 0


def test_assembly_grants_no_permission_and_cannot_bypass_action_gate() -> None:
    strategy = HostileStrategy()
    authority = AuthorityContext(frozenset({Permission.READ}))
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Destructive external operation remains high risk.",
        reversible=False,
        external_effect=True,
        destructive=True,
    )
    request = GateRequest(
        operation="future.external.write",
        required_permission=Permission.WRITE,
        risk_assessment=assessment,
    )
    gate = ActionGate()
    before = gate.evaluate(request, authority)

    _assemble(strategy)

    after = gate.evaluate(request, authority)
    assert before.decision is GateDecision.DENY
    assert after == before
    assert authority.permissions == frozenset({Permission.READ})
    assert PermissionEngine().check(Permission.WRITE, authority).present is False
    assert assessment.effective_level is RiskLevel.R4


def test_assembly_does_not_lower_risk_or_consume_or_enlarge_budget() -> None:
    strategy = HostileStrategy()
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Critical operation remains critical.",
        reversible=False,
        external_effect=True,
        critical=True,
    )
    envelope = _envelope()
    budget = ResourceBudget(envelope)
    before = budget.snapshot()

    _assemble(strategy)

    assert assessment.effective_level is RiskLevel.R4
    assert budget.envelope == envelope
    assert budget.snapshot() == before
    assert strategy.model_calls == 0


def test_assembly_cannot_clear_emergency_stop_or_transition_task() -> None:
    strategy = HostileStrategy()
    stop = EmergencyStop()
    stop.request_stop()
    task = Task.create("Strategy assembly must not transition this task.")
    before = task.to_json()

    _assemble(strategy)

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True
    assert task.status is TaskStatus.PENDING
    assert task.to_json() == before


def test_verified_text_cannot_change_canonical_verification_result() -> None:
    strategy = HostileStrategy()
    verification = VerificationResult(passed=False, detail="canonical verification failed")

    assembly = _assemble(strategy)

    assert verification.passed is False
    assert verification.detail == "canonical verification failed"
    assert not hasattr(assembly, "verified")
    assert not hasattr(assembly, "verification")
    assert not hasattr(assembly, "passed")
    assert strategy.attempt_calls == 0


def test_assembly_invokes_no_strategy_model_capability_procedure_or_persistence() -> None:
    strategy = HostileStrategy()

    assembly = _assemble(strategy)

    assert assembly.levels == (ExecutionLevel.L4_PLANNED,)
    assert strategy.attempt_calls == 0
    assert strategy.model_calls == 0
    assert strategy.capability_calls == 0
    assert strategy.procedure_calls == 0
    assert strategy.persistence_calls == 0
