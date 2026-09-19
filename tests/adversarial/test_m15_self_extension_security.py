from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityPlatform,
    CapabilityVersion,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk
from agentx.self_extension import (
    CandidateArtifact,
    CandidateProvenance,
    CandidateSourceType,
    CapabilityDesignProposal,
    DependencyPolicy,
    GeneratedDependency,
    GeneratedToolSandbox,
    GeneratedToolSpecification,
    OutputField,
    OutputType,
    SelfExtensionManager,
    SelfExtensionSecurityError,
    TrustedApprovalAuthority,
    ValidationCase,
    inspect_candidate,
    trusted_kernel_fingerprint,
    validate_candidate,
)

NOW = datetime(2026, 9, 19, 7, 30, tzinfo=UTC)
KEY = b"m15-adversarial-integrity-key-0001"


def proposal() -> CapabilityDesignProposal:
    return CapabilityDesignProposal(
        proposal_id=uuid4(),
        objective_id=uuid4(),
        identity=CapabilityIdentity(
            name=CapabilityName("generated.safe"),
            version=CapabilityVersion(1, 0, 0),
        ),
        description="Adversarial M15 test candidate.",
        platform=CapabilityPlatform.ANY,
        required_permissions=frozenset(),
        risk_assessment=assess_risk(
            read_only=True,
            modifies_state=False,
            reversible=False,
            external_effect=False,
        ),
        specification=GeneratedToolSpecification(
            input_keys=("x",),
            output_fields=(OutputField("answer", OutputType.INTEGER),),
        ),
        created_at=NOW,
    )


def candidate(
    source: str,
    *,
    dependencies: tuple[GeneratedDependency, ...] = (),
) -> CandidateArtifact:
    return CandidateArtifact.create(
        proposal=proposal(),
        source=source,
        provenance=CandidateProvenance(
            source_type=CandidateSourceType.RESEARCH_ASSISTED,
            source="https://untrusted.invalid/repository",
            retrieved_at=NOW,
            generator="untrusted-model-output",
            requesting_task="attack-task",
        ),
        dependencies=dependencies,
    )


@pytest.mark.parametrize(
    "source",
    (
        'def run(payload):\n    return {"answer": eval("1+1")}\n',
        'def run(payload):\n    return {"answer": exec("x=1")}\n',
        'def run(payload):\n    return {"answer": __import__("subprocess")}\n',
        'def run(payload):\n    return {"answer": __import__("socket")}\n',
        'def run(payload):\n    return {"answer": open("../kernel/action_gate.py").read()}\n',
        'def run(payload):\n    return {"answer": globals()}\n',
        'def run(payload):\n    return {"answer": payload.__class__.__mro__}\n',
        'def run(payload):\n    __import__("os").environ["X"]="1"\n    return {"answer": 1}\n',
        "def run(payload):\n    while True:\n        pass\n",
        'def run(payload):\n    return {"answer": (lambda: 1)()}\n',
    ),
)
def test_authority_escape_sources_are_rejected_before_execution(source: str) -> None:
    before = trusted_kernel_fingerprint()
    report = inspect_candidate(candidate(source))
    after = trusted_kernel_fingerprint()
    assert not report.passed
    assert before == after


def test_candidate_cannot_request_execute_permission_or_downgrade_real_risk() -> None:
    with pytest.raises(SelfExtensionSecurityError):
        CapabilityDesignProposal(
            proposal_id=uuid4(),
            objective_id=uuid4(),
            identity=proposal().identity,
            description="Try to smuggle execution authority.",
            platform=CapabilityPlatform.ANY,
            required_permissions=frozenset({Permission.EXECUTE}),
            risk_assessment=assess_risk(
                read_only=True,
                modifies_state=False,
                reversible=False,
                external_effect=False,
            ),
            specification=proposal().specification,
            created_at=NOW,
        )

    with pytest.raises(SelfExtensionSecurityError):
        CapabilityDesignProposal(
            proposal_id=uuid4(),
            objective_id=uuid4(),
            identity=proposal().identity,
            description="Try to label an external effect as generated.",
            platform=CapabilityPlatform.ANY,
            required_permissions=frozenset(),
            risk_assessment=assess_risk(
                read_only=False,
                modifies_state=True,
                reversible=False,
                external_effect=True,
            ),
            specification=proposal().specification,
            created_at=NOW,
        )


def test_dependency_substitution_is_rejected() -> None:
    approved = GeneratedDependency(
        name="example",
        version="1.0.0",
        digest="sha256:" + ("1" * 64),
    )
    substituted = GeneratedDependency(
        name="example",
        version="1.0.0",
        digest="sha256:" + ("2" * 64),
    )
    value = candidate(
        'def run(payload):\n    return {"answer": payload["x"]}\n',
        dependencies=(substituted,),
    )
    evidence = validate_candidate(
        artifact=value,
        cases=(
            ValidationCase(name="a", payload={"x": 1}, expected={"answer": 1}),
            ValidationCase(name="b", payload={"x": 2}, expected={"answer": 2}),
            ValidationCase(name="c", payload={"x": 3}, expected={"answer": 3}),
        ),
        sandbox=GeneratedToolSandbox(),
        dependency_policy=DependencyPolicy(approved=(approved,)),
        validated_at=NOW,
    )
    assert not evidence.dependency_passed
    assert not evidence.passed


def test_persistence_tampering_and_forged_activation_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "extensions.json"
    authority = TrustedApprovalAuthority(authority_id="host", key=KEY)
    manager = SelfExtensionManager(
        registry=CapabilityRegistry(),
        sandbox=GeneratedToolSandbox(),
        approval_authority=authority,
        integrity_key=KEY,
        persistence_path=path,
    )
    assert manager.lifecycle(proposal().identity) is None

    path.write_text(
        '{"schema":1,"records":[],"mac":"sha256:' + ("0" * 64) + '"}',
        encoding="utf-8",
    )
    with pytest.raises(SelfExtensionSecurityError):
        SelfExtensionManager(
            registry=CapabilityRegistry(),
            sandbox=GeneratedToolSandbox(),
            approval_authority=authority,
            integrity_key=KEY,
            persistence_path=path,
        )


def test_self_extension_module_exposes_no_generic_exec_or_eval_call() -> None:
    import agentx.self_extension as module

    module_path = Path(module.__file__ or "")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile"}
        ):
            forbidden.append(node.func.id)
    assert forbidden == []
