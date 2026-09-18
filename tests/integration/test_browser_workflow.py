"""Integration coverage for bounded governed browser workflow composition."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agentx.browser_workflow import BrowserWorkflow, BrowserWorkflowStep
from agentx.capabilities.browser_forms import (
    BrowserFormOperation,
    BrowserFormsCapability,
    select_option_request,
    set_checked_request,
)
from agentx.capabilities.executor import ExecutorRequest
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.kernel.permissions import Permission
from tests.support.demo_capability import NoteWriteParams, write_request
from tests.support.orchestration_harness import OrchestrationHarness
from tests.unit.test_browser_forms_session_recovery import _node, _Surface


def test_multi_field_browser_workflow_uses_shared_governed_executor_and_budget(
    tmp_path: Path,
) -> None:
    surface = _Surface(tmp_path)
    harness = OrchestrationHarness(
        authority=frozenset({Permission.WRITE}),
        register_capability=False,
    )
    for operation in (
        BrowserFormOperation.SET_CHECKED,
        BrowserFormOperation.SELECT_OPTION,
    ):
        harness.registry.register(
            BrowserFormsCapability(
                operation=operation,
                provider=surface,
                driver=surface,
            )
        )

    correlation_id = uuid4()
    target = surface.target
    checkbox = _node(target, "checkbox")
    select = _node(target, "select")
    first_task = harness.make_task("set the governed checkbox")
    second_task = harness.make_task("select the governed option")
    first_context = ExecutionContext(
        correlation_id=correlation_id,
        cancellation_token=CancellationSource().token,
        task_id=first_task.task_id,
    )
    second_context = ExecutionContext(
        correlation_id=correlation_id,
        cancellation_token=CancellationSource().token,
        task_id=second_task.task_id,
    )

    result = BrowserWorkflow(harness.executor).run(
        (
            BrowserWorkflowStep(
                ExecutorRequest(
                    first_task,
                    set_checked_request(target, checkbox, True),
                    first_context,
                )
            ),
            BrowserWorkflowStep(
                ExecutorRequest(
                    second_task,
                    select_option_request(target, select, "private-option"),
                    second_context,
                )
            ),
        )
    ).unwrap()

    assert result.completed is True
    assert len(result.outcomes) == 2
    assert surface.calls == ["set_checked", "select_option"]
    assert harness.budget.snapshot().machine_actions == 2


def test_browser_workflow_rejects_non_browser_capability(tmp_path: Path) -> None:
    harness = OrchestrationHarness()
    task = harness.make_task()
    request = ExecutorRequest(
        task,
        write_request(NoteWriteParams(key="alpha", value="v1")),
        harness.make_context(task),
    )
    with pytest.raises(ValueError, match=r"browser\.\*"):
        BrowserWorkflowStep(request)
