"""Unit tests for the canonical AgentX Task state machine (``agentx.core.task_state``).

Covers:
    - the exhaustive 5 x 5 transition matrix (25 pairs, 5 legal / 20 invalid)
    - terminal-state semantics for SUCCEEDED / FAILED / CANCELLED
    - rejection of every self-transition
    - determinism and absence of hidden state or hidden mutation
    - new-Task derivation: unrelated fields preserved, original Task untouched
    - explicit, narrow error semantics for invalid transitions
    - priority being data, never transition authority
    - module scope: transitions only, core-only imports, no Task Manager /
      execution / scheduling / event / persistence surface
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from agentx.core.errors import AgentXError, AgentXException, ErrorCategory, Retryability
from agentx.core.result import Failure, InvalidResultError, Result, Success
from agentx.core.task_state import (
    LEGAL_TASK_TRANSITIONS,
    TASK_TRANSITION_ERROR_CODE,
    TERMINAL_TASK_STATUSES,
    InvalidTaskTransitionError,
    can_transition,
    is_terminal,
    legal_transitions,
    transition_error,
    transition_task,
    try_transition_task,
    validate_transition,
)
from agentx.core.tasks import Task, TaskPriority, TaskStatus

_MODULE = Path(__file__).resolve().parents[2] / "src" / "agentx" / "core" / "task_state.py"

_ALL_STATUSES: tuple[TaskStatus, ...] = tuple(TaskStatus)
_ALL_PAIRS: tuple[tuple[TaskStatus, TaskStatus], ...] = tuple(
    (current, target) for current in TaskStatus for target in TaskStatus
)

# Independent restatement of the canonical A1.06 contract. This is deliberately
# a literal: the tests must not re-derive their expectations from the production
# table they are checking.
_CANONICAL_LEGAL_PAIRS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
    }
)

_CANONICAL_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

_FIXED_CREATED_AT = datetime(2026, 9, 4, 12, 30, 45, 123456, tzinfo=UTC)

_NESTED_METADATA: dict[str, object] = {
    "origin": "goal-decomposition",
    "attempt": 2,
    "weights": [1, 2, 3],
    "nested": {"window": "inbox", "flags": ["urgent", "local"]},
    "empty": None,
}


def _status_sort_key(status: TaskStatus) -> str:
    """Stable ordering key so parametrized test ids are deterministic."""
    return status.value


def _pair_sort_key(pair: tuple[TaskStatus, TaskStatus]) -> str:
    """Stable ordering key so parametrized test ids are deterministic."""
    return f"{pair[0].value}->{pair[1].value}"


# Ordered copies for ``pytest.mark.parametrize``. The sort keys are named
# functions (annotated at the definition) and the results are explicitly
# annotated, so parametrized test ids are deterministic and type-checked.
_ALL_STATUSES_ORDERED: tuple[TaskStatus, ...] = tuple(sorted(_ALL_STATUSES, key=_status_sort_key))
_TERMINAL_STATUSES_ORDERED: tuple[TaskStatus, ...] = tuple(
    sorted(_CANONICAL_TERMINAL_STATUSES, key=_status_sort_key)
)
_LEGAL_PAIRS_ORDERED: tuple[tuple[TaskStatus, TaskStatus], ...] = tuple(
    sorted(_CANONICAL_LEGAL_PAIRS, key=_pair_sort_key)
)
_INVALID_PAIRS_ORDERED: tuple[tuple[TaskStatus, TaskStatus], ...] = tuple(
    sorted((pair for pair in _ALL_PAIRS if pair not in _CANONICAL_LEGAL_PAIRS), key=_pair_sort_key)
)


def _task(
    *,
    status: TaskStatus = TaskStatus.PENDING,
    priority: TaskPriority = TaskPriority.NORMAL,
    metadata: dict[str, object] | None = None,
    parent: bool = False,
) -> Task:
    """Build a deterministic Task with every field explicitly populated."""
    parent_task_id = None
    if parent:
        parent_task_id = Task.create(objective="parent objective").task_id
    return Task(
        task_id=Task.create(objective="placeholder").task_id,
        objective="summarize the inbox and draft a reply",
        status=status,
        priority=priority,
        parent_task_id=parent_task_id,
        created_at=_FIXED_CREATED_AT,
        metadata=_NESTED_METADATA if metadata is None else metadata,
    )


def _code_without_docstrings() -> str:
    """Return the module's code with every docstring removed, lowercased."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


def _module_imports() -> set[str]:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            imported.add(node.module)
    return imported


# ---------------------------------------------------------------------------
# The canonical matrix
# ---------------------------------------------------------------------------


class TestTransitionMatrix:
    def test_matrix_is_the_full_five_by_five_grid(self) -> None:
        """The contract is checked over all 25 status pairs, not a sample."""
        assert len(_ALL_STATUSES) == 5
        assert len(_ALL_PAIRS) == 25

    def test_exactly_the_five_canonical_pairs_are_legal(self) -> None:
        """The set of legal pairs is precisely the canonical v1 set."""
        legal = {pair for pair in _ALL_PAIRS if can_transition(*pair)}
        assert legal == _CANONICAL_LEGAL_PAIRS

    @pytest.mark.parametrize(("current", "target"), _ALL_PAIRS)
    def test_can_transition_matches_canonical_contract(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        """Every cell of the 5 x 5 matrix returns the canonical verdict."""
        assert can_transition(current, target) is ((current, target) in _CANONICAL_LEGAL_PAIRS)

    def test_pending_targets(self) -> None:
        assert legal_transitions(TaskStatus.PENDING) == frozenset(
            {TaskStatus.RUNNING, TaskStatus.CANCELLED}
        )

    def test_running_targets(self) -> None:
        assert legal_transitions(TaskStatus.RUNNING) == frozenset(
            {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        )

    def test_table_covers_every_status_in_the_vocabulary(self) -> None:
        """No status is missing from the table, so nothing defaults to allowed."""
        assert set(LEGAL_TASK_TRANSITIONS) == set(TaskStatus)

    def test_table_targets_are_vocabulary_members(self) -> None:
        for targets in LEGAL_TASK_TRANSITIONS.values():
            assert isinstance(targets, frozenset)
            assert targets <= set(TaskStatus)

    def test_no_status_lists_itself_as_a_target(self) -> None:
        for status, targets in LEGAL_TASK_TRANSITIONS.items():
            assert status not in targets

    def test_table_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            LEGAL_TASK_TRANSITIONS[TaskStatus.PENDING] = frozenset()  # type: ignore[index]

    def test_returned_target_sets_cannot_be_widened(self) -> None:
        targets = legal_transitions(TaskStatus.PENDING)
        assert not hasattr(targets, "add")
        assert not hasattr(targets, "remove")

    def test_terminal_set_is_derived_from_the_table(self) -> None:
        derived = frozenset(status for status in TaskStatus if not legal_transitions(status))
        assert derived == TERMINAL_TASK_STATUSES
        assert TERMINAL_TASK_STATUSES == _CANONICAL_TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Legal transitions succeed
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_validate_transition_accepts_legal_pairs(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        # The contract is "does not raise". ``validate_transition`` declares a
        # ``None`` return type, so there is deliberately no value to assert on.
        validate_transition(current, target)

    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_transition_task_derives_the_target_status(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        task = _task(status=current)

        derived = transition_task(task, target)

        assert derived.status is target
        assert derived.task_id == task.task_id

    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_try_transition_task_succeeds(self, current: TaskStatus, target: TaskStatus) -> None:
        result = try_transition_task(_task(status=current), target)

        assert isinstance(result, Success)
        assert result.is_success is True
        assert result.unwrap().status is target

    def test_pending_to_running(self) -> None:
        derived = transition_task(_task(status=TaskStatus.PENDING), TaskStatus.RUNNING)
        assert derived.status is TaskStatus.RUNNING

    def test_pending_to_cancelled(self) -> None:
        derived = transition_task(_task(status=TaskStatus.PENDING), TaskStatus.CANCELLED)
        assert derived.status is TaskStatus.CANCELLED

    def test_running_to_succeeded(self) -> None:
        derived = transition_task(_task(status=TaskStatus.RUNNING), TaskStatus.SUCCEEDED)
        assert derived.status is TaskStatus.SUCCEEDED

    def test_running_to_failed(self) -> None:
        derived = transition_task(_task(status=TaskStatus.RUNNING), TaskStatus.FAILED)
        assert derived.status is TaskStatus.FAILED

    def test_running_to_cancelled(self) -> None:
        derived = transition_task(_task(status=TaskStatus.RUNNING), TaskStatus.CANCELLED)
        assert derived.status is TaskStatus.CANCELLED

    def test_legal_chain_produces_the_expected_history(self) -> None:
        """A whole legal path can be walked, one explicit request at a time."""
        task = _task(status=TaskStatus.PENDING)
        history = [task.status]

        for target in (TaskStatus.RUNNING, TaskStatus.SUCCEEDED):
            task = transition_task(task, target)
            history.append(task.status)

        assert history == [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.SUCCEEDED]
        assert is_terminal(task.status) is True


# ---------------------------------------------------------------------------
# Invalid transitions fail
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        _INVALID_PAIRS_ORDERED,
    )
    def test_can_transition_is_false(self, current: TaskStatus, target: TaskStatus) -> None:
        assert can_transition(current, target) is False

    @pytest.mark.parametrize(
        ("current", "target"),
        _INVALID_PAIRS_ORDERED,
    )
    def test_validate_transition_rejects(self, current: TaskStatus, target: TaskStatus) -> None:
        with pytest.raises(InvalidTaskTransitionError) as excinfo:
            validate_transition(current, target)

        assert excinfo.value.error.code == TASK_TRANSITION_ERROR_CODE

    @pytest.mark.parametrize(
        ("current", "target"),
        _INVALID_PAIRS_ORDERED,
    )
    def test_transition_task_rejects_and_leaves_task_alone(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        task = _task(status=current)
        before = task.to_dict()

        with pytest.raises(InvalidTaskTransitionError):
            transition_task(task, target)

        assert task.status is current
        assert task.to_dict() == before

    @pytest.mark.parametrize(
        ("current", "target"),
        _INVALID_PAIRS_ORDERED,
    )
    def test_try_transition_task_fails_without_raising(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        result = try_transition_task(_task(status=current), target)

        assert isinstance(result, Failure)
        assert result.is_failure is True
        assert result.unwrap_error() == transition_error(current, target)
        with pytest.raises(InvalidResultError):
            result.unwrap()

    def test_backward_transition_pending_from_running_is_invalid(self) -> None:
        assert can_transition(TaskStatus.RUNNING, TaskStatus.PENDING) is False

    def test_skipping_a_state_is_invalid(self) -> None:
        assert can_transition(TaskStatus.PENDING, TaskStatus.SUCCEEDED) is False
        assert can_transition(TaskStatus.PENDING, TaskStatus.FAILED) is False

    def test_reopening_a_terminal_task_is_invalid(self) -> None:
        assert can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING) is False
        assert can_transition(TaskStatus.FAILED, TaskStatus.RUNNING) is False
        assert can_transition(TaskStatus.CANCELLED, TaskStatus.PENDING) is False


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminalStates:
    @pytest.mark.parametrize("status", _TERMINAL_STATUSES_ORDERED)
    def test_is_terminal_true_for_terminal_statuses(self, status: TaskStatus) -> None:
        assert is_terminal(status) is True

    @pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.RUNNING])
    def test_is_terminal_false_for_non_terminal_statuses(self, status: TaskStatus) -> None:
        assert is_terminal(status) is False

    def test_terminal_vocabulary_is_exactly_the_canonical_three(self) -> None:
        assert TERMINAL_TASK_STATUSES == _CANONICAL_TERMINAL_STATUSES
        assert {status for status in TaskStatus if is_terminal(status)} == set(
            _CANONICAL_TERMINAL_STATUSES
        )

    @pytest.mark.parametrize("status", _TERMINAL_STATUSES_ORDERED)
    def test_terminal_states_have_no_outgoing_transitions(self, status: TaskStatus) -> None:
        assert legal_transitions(status) == frozenset()
        assert all(can_transition(status, target) is False for target in TaskStatus)

    @pytest.mark.parametrize("status", _TERMINAL_STATUSES_ORDERED)
    def test_transition_task_rejects_every_move_from_a_terminal_state(
        self, status: TaskStatus
    ) -> None:
        task = _task(status=status)

        for target in TaskStatus:
            with pytest.raises(InvalidTaskTransitionError):
                transition_task(task, target)
            assert try_transition_task(task, target).is_failure is True

        assert task.status is status


# ---------------------------------------------------------------------------
# Self-transitions
# ---------------------------------------------------------------------------


class TestSelfTransitions:
    @pytest.mark.parametrize("status", _ALL_STATUSES_ORDERED)
    def test_self_transition_is_invalid(self, status: TaskStatus) -> None:
        assert can_transition(status, status) is False
        assert status not in legal_transitions(status)

    @pytest.mark.parametrize("status", _ALL_STATUSES_ORDERED)
    def test_self_transition_is_rejected_by_every_api(self, status: TaskStatus) -> None:
        task = _task(status=status)

        with pytest.raises(InvalidTaskTransitionError):
            validate_transition(status, status)
        with pytest.raises(InvalidTaskTransitionError):
            transition_task(task, status)
        assert try_transition_task(task, status).is_failure is True
        assert task.status is status

    def test_self_transition_error_is_the_shared_rejection_error(self) -> None:
        error = transition_error(TaskStatus.RUNNING, TaskStatus.RUNNING)

        assert error.code == TASK_TRANSITION_ERROR_CODE
        assert error.category is ErrorCategory.CONFLICT
        assert error.details["current_status"] == "running"
        assert error.details["target_status"] == "running"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(("current", "target"), _ALL_PAIRS)
    def test_repeated_evaluation_is_stable(self, current: TaskStatus, target: TaskStatus) -> None:
        expected = (current, target) in _CANONICAL_LEGAL_PAIRS
        assert {can_transition(current, target) for _ in range(200)} == {expected}

    def test_interleaved_requests_do_not_change_later_answers(self) -> None:
        """No hidden state: mixing legal and illegal calls changes nothing."""
        sequence = [
            (TaskStatus.PENDING, TaskStatus.RUNNING),
            (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),
            (TaskStatus.PENDING, TaskStatus.RUNNING),
            (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
            (TaskStatus.RUNNING, TaskStatus.RUNNING),
            (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        ]
        first = [can_transition(*pair) for pair in sequence]
        second = [can_transition(*pair) for pair in reversed(sequence)]
        third = [can_transition(*pair) for pair in sequence]

        assert first == [True, False, True, True, False, True]
        assert second == list(reversed(first))
        assert third == first

    @pytest.mark.parametrize(("current", "target"), _ALL_PAIRS)
    def test_rejected_transitions_produce_equal_errors(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        first = transition_error(current, target)
        second = transition_error(current, target)

        assert first == second
        assert first.to_dict() == second.to_dict()
        assert first.details == second.details

    def test_derived_tasks_are_equal_for_equal_inputs(self) -> None:
        source = _task(status=TaskStatus.PENDING)

        first = transition_task(source, TaskStatus.RUNNING)
        second = transition_task(source, TaskStatus.RUNNING)

        assert first == second
        assert first.to_json() == second.to_json()
        assert source.status is TaskStatus.PENDING

    def test_results_are_equal_for_equal_inputs(self) -> None:
        source = _task(status=TaskStatus.PENDING)

        first: Result[Task, AgentXError] = try_transition_task(source, TaskStatus.RUNNING)
        second: Result[Task, AgentXError] = try_transition_task(source, TaskStatus.RUNNING)

        assert first == second
        assert first.unwrap() == second.unwrap()
        assert first.unwrap().status is second.unwrap().status

    def test_evaluation_leaves_no_module_state_behind(self) -> None:
        module = importlib.import_module("agentx.core.task_state")
        before = {name: value for name, value in vars(module).items() if not name.startswith("__")}

        for current, target in _ALL_PAIRS:
            can_transition(current, target)
            try_transition_task(_task(status=current), target)

        after = {name: value for name, value in vars(module).items() if not name.startswith("__")}
        assert after == before

    def test_evaluation_does_not_mutate_the_contract(self) -> None:
        snapshot = {
            status: frozenset(targets) for status, targets in LEGAL_TASK_TRANSITIONS.items()
        }

        for current, target in _ALL_PAIRS:
            can_transition(current, target)

        assert dict(LEGAL_TASK_TRANSITIONS) == snapshot


# ---------------------------------------------------------------------------
# Derived Task: field preservation and immutability
# ---------------------------------------------------------------------------


class TestDerivedTask:
    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_returns_a_new_task_object(self, current: TaskStatus, target: TaskStatus) -> None:
        task = _task(status=current)

        derived = transition_task(task, target)

        assert derived is not task
        assert isinstance(derived, Task)

    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_only_status_differs(self, current: TaskStatus, target: TaskStatus) -> None:
        task = _task(status=current, parent=True)

        derived = transition_task(task, target)

        before = task.to_dict()
        after = derived.to_dict()
        assert before.keys() == after.keys()
        changed = {key for key in before if before[key] != after[key]}
        assert changed == {"status"}
        assert after["status"] == target.value

    @pytest.mark.parametrize(("current", "target"), _LEGAL_PAIRS_ORDERED)
    def test_every_unrelated_field_is_preserved(
        self, current: TaskStatus, target: TaskStatus
    ) -> None:
        task = _task(status=current, priority=TaskPriority.CRITICAL, parent=True)

        derived = transition_task(task, target)

        assert derived.task_id is task.task_id
        assert derived.objective == task.objective
        assert derived.priority is task.priority
        assert derived.parent_task_id == task.parent_task_id
        assert derived.created_at == task.created_at
        assert derived.metadata == task.metadata

    def test_identity_and_hierarchy_are_carried_over(self) -> None:
        parent_task_id = Task.create(objective="parent objective").task_id
        task = _task(status=TaskStatus.PENDING)
        task = Task(
            task_id=task.task_id,
            objective=task.objective,
            status=task.status,
            priority=task.priority,
            parent_task_id=parent_task_id,
            created_at=task.created_at,
            metadata=task.metadata,
        )

        derived = transition_task(transition_task(task, TaskStatus.RUNNING), TaskStatus.FAILED)

        assert derived.task_id_str == task.task_id_str
        assert derived.parent_task_id == parent_task_id
        assert derived.is_child_of(parent_task_id) is True
        assert derived.is_root is False

    def test_creation_timestamp_is_preserved_exactly(self) -> None:
        offset_created_at = datetime(
            2026, 9, 4, 14, 30, 45, 123456, tzinfo=timezone(timedelta(hours=2))
        )
        task = Task.create(
            objective="summarize the inbox",
            status=TaskStatus.PENDING,
            created_at=offset_created_at,
            metadata=dict(_NESTED_METADATA),
        )

        derived = transition_task(task, TaskStatus.RUNNING)

        assert derived.created_at == task.created_at
        assert derived.created_at.utcoffset() == timedelta(0)
        assert derived.to_dict()["created_at"] == task.to_dict()["created_at"]
        assert derived.to_dict()["created_at"] == "2026-09-04T12:30:45.123456Z"

    def test_metadata_is_preserved_by_value_and_stays_immutable(self) -> None:
        task = _task(status=TaskStatus.PENDING)

        derived = transition_task(task, TaskStatus.RUNNING)

        assert dict(derived.metadata) == dict(task.metadata)
        assert derived.metadata["origin"] == "goal-decomposition"
        assert derived.metadata["weights"] == (1, 2, 3)
        assert isinstance(derived.metadata, MappingProxyType)
        with pytest.raises(TypeError):
            derived.metadata["origin"] = "mutated"  # type: ignore[index]

    def test_original_task_is_not_mutated(self) -> None:
        task = _task(status=TaskStatus.PENDING)
        before = task.to_json()

        derived = transition_task(task, TaskStatus.RUNNING)

        assert task.status is TaskStatus.PENDING
        assert task.to_json() == before
        assert derived.status is TaskStatus.RUNNING

    def test_input_task_remains_frozen_after_transition(self) -> None:
        task = _task(status=TaskStatus.PENDING)
        transition_task(task, TaskStatus.RUNNING)

        with pytest.raises(FrozenInstanceError):
            task.status = TaskStatus.FAILED  # type: ignore[misc]

    def test_derived_task_is_also_frozen(self) -> None:
        derived = transition_task(_task(status=TaskStatus.PENDING), TaskStatus.RUNNING)

        with pytest.raises(FrozenInstanceError):
            derived.status = TaskStatus.SUCCEEDED  # type: ignore[misc]

    def test_derived_task_survives_a_serialization_round_trip(self) -> None:
        task = _task(status=TaskStatus.RUNNING, parent=True)

        derived = transition_task(task, TaskStatus.SUCCEEDED)

        assert Task.from_json(derived.to_json()) == derived

    def test_try_transition_result_carries_the_derived_task(self) -> None:
        task = _task(status=TaskStatus.PENDING)

        result = try_transition_task(task, TaskStatus.RUNNING)

        derived = result.unwrap()
        assert derived.status is TaskStatus.RUNNING
        assert derived is not task
        assert derived.task_id == task.task_id
        assert task.status is TaskStatus.PENDING

    def test_transition_happens_only_when_explicitly_requested(self) -> None:
        """Reading the contract never changes a Task."""
        task = _task(status=TaskStatus.PENDING)

        can_transition(task.status, TaskStatus.RUNNING)
        legal_transitions(task.status)
        is_terminal(task.status)
        validate_transition(task.status, TaskStatus.RUNNING)
        transition_error(task.status, TaskStatus.SUCCEEDED)

        assert task.status is TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------


class TestErrorSemantics:
    def test_invalid_transition_raises_the_narrow_exception(self) -> None:
        with pytest.raises(InvalidTaskTransitionError) as excinfo:
            transition_task(_task(status=TaskStatus.SUCCEEDED), TaskStatus.RUNNING)

        assert isinstance(excinfo.value, AgentXException)

    def test_raised_error_carries_the_structured_error(self) -> None:
        with pytest.raises(InvalidTaskTransitionError) as excinfo:
            transition_task(_task(status=TaskStatus.SUCCEEDED), TaskStatus.RUNNING)

        error = excinfo.value.error
        assert isinstance(error, AgentXError)
        assert error.code == TASK_TRANSITION_ERROR_CODE
        assert error.category is ErrorCategory.CONFLICT
        assert error.retryability is Retryability.NON_RETRYABLE
        assert error.details["current_status"] == "succeeded"
        assert error.details["target_status"] == "running"
        assert error.details["allowed_targets"] == []

    def test_error_names_the_rejected_pair(self) -> None:
        error = transition_error(TaskStatus.PENDING, TaskStatus.SUCCEEDED)

        assert "pending -> succeeded" in error.message
        assert str(error) == f"[{TASK_TRANSITION_ERROR_CODE}] {error.message}"

    def test_error_lists_the_allowed_targets(self) -> None:
        error = transition_error(TaskStatus.RUNNING, TaskStatus.PENDING)

        assert error.details["allowed_targets"] == ["cancelled", "failed", "succeeded"]

    def test_error_is_serializable(self) -> None:
        payload = transition_error(TaskStatus.RUNNING, TaskStatus.RUNNING).to_dict()

        assert payload["code"] == TASK_TRANSITION_ERROR_CODE
        assert payload["category"] == "conflict"
        assert payload["retryability"] == "non_retryable"
        assert payload["details"] == {
            "current_status": "running",
            "target_status": "running",
            "allowed_targets": ["cancelled", "failed", "succeeded"],
        }

    def test_failure_result_matches_the_raised_error(self) -> None:
        task = _task(status=TaskStatus.CANCELLED)

        result = try_transition_task(task, TaskStatus.RUNNING)

        with pytest.raises(InvalidTaskTransitionError) as excinfo:
            transition_task(task, TaskStatus.RUNNING)
        assert result.unwrap_error() == excinfo.value.error

    def test_invalid_transitions_are_never_silently_ignored(self) -> None:
        """No API returns an unchanged Task to signal a rejected move."""
        task = _task(status=TaskStatus.FAILED)

        result = try_transition_task(task, TaskStatus.RUNNING)

        assert result.is_failure is True
        assert result.is_success is False
        with pytest.raises(InvalidResultError):
            result.unwrap()

    @pytest.mark.parametrize("api", ["can_transition", "validate_transition", "transition_error"])
    def test_status_strings_are_not_coerced(self, api: str) -> None:
        """Raw strings are a programming error, never a silent status."""
        function = {
            "can_transition": can_transition,
            "validate_transition": validate_transition,
            "transition_error": transition_error,
        }[api]

        with pytest.raises(TypeError, match="must be a TaskStatus"):
            function("pending", "running")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="must be a TaskStatus"):
            function(TaskStatus.PENDING, "running")  # type: ignore[arg-type]

    @pytest.mark.parametrize("api", ["transition_task", "try_transition_task"])
    def test_task_apis_reject_non_task_values(self, api: str) -> None:
        function = {"transition_task": transition_task, "try_transition_task": try_transition_task}[
            api
        ]

        with pytest.raises(TypeError, match="task must be a Task"):
            function({"status": "pending"}, TaskStatus.RUNNING)  # type: ignore[arg-type]

    @pytest.mark.parametrize("api", ["transition_task", "try_transition_task"])
    def test_task_apis_reject_raw_status_strings(self, api: str) -> None:
        function = {"transition_task": transition_task, "try_transition_task": try_transition_task}[
            api
        ]

        with pytest.raises(TypeError, match="target must be a TaskStatus"):
            function(_task(status=TaskStatus.PENDING), "running")  # type: ignore[arg-type]

    def test_read_apis_reject_non_task_status(self) -> None:
        with pytest.raises(TypeError, match="status must be a TaskStatus"):
            legal_transitions("pending")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="status must be a TaskStatus"):
            is_terminal("pending")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Priority is data, never transition authority
# ---------------------------------------------------------------------------


class TestPriorityIsNotAuthority:
    @pytest.mark.parametrize("priority", list(TaskPriority))
    @pytest.mark.parametrize(("current", "target"), _ALL_PAIRS)
    def test_legality_is_independent_of_priority(
        self, priority: TaskPriority, current: TaskStatus, target: TaskStatus
    ) -> None:
        expected = (current, target) in _CANONICAL_LEGAL_PAIRS

        assert can_transition(current, target) is expected
        assert try_transition_task(_task(status=current, priority=priority), target).is_success is (
            expected
        )

    @pytest.mark.parametrize("priority", list(TaskPriority))
    def test_critical_and_low_get_the_same_machine(self, priority: TaskPriority) -> None:
        baseline = {pair: can_transition(*pair) for pair in _ALL_PAIRS}
        for pair in _ALL_PAIRS:
            critical = _task(status=pair[0], priority=TaskPriority.CRITICAL)
            other = _task(status=pair[0], priority=priority)
            assert try_transition_task(critical, pair[1]).is_success is baseline[pair]
            assert try_transition_task(other, pair[1]).is_success is baseline[pair]

    @pytest.mark.parametrize("priority", list(TaskPriority))
    def test_legal_transition_succeeds_for_every_priority(self, priority: TaskPriority) -> None:
        derived = transition_task(_task(priority=priority), TaskStatus.RUNNING)

        assert derived.status is TaskStatus.RUNNING
        assert derived.priority is priority

    @pytest.mark.parametrize("priority", list(TaskPriority))
    def test_terminal_state_blocks_every_priority(self, priority: TaskPriority) -> None:
        task = _task(status=TaskStatus.SUCCEEDED, priority=priority)

        with pytest.raises(InvalidTaskTransitionError) as excinfo:
            transition_task(task, TaskStatus.RUNNING)

        assert excinfo.value.error.code == TASK_TRANSITION_ERROR_CODE
        assert "priority" not in {str(key) for key in excinfo.value.error.details}

    def test_priority_is_never_part_of_the_error_or_the_contract(self) -> None:
        error = transition_error(TaskStatus.CANCELLED, TaskStatus.RUNNING)

        assert "priority" not in error.message
        assert "priority" not in error.details
        assert set(error.details) == {"current_status", "target_status", "allowed_targets"}

    def test_priority_value_is_preserved_across_transitions(self) -> None:
        task = _task(status=TaskStatus.PENDING, priority=TaskPriority.CRITICAL)

        derived = transition_task(transition_task(task, TaskStatus.RUNNING), TaskStatus.FAILED)

        assert derived.priority is TaskPriority.CRITICAL


# ---------------------------------------------------------------------------
# Scope and architecture
# ---------------------------------------------------------------------------


class TestScope:
    def test_module_imports_only_stdlib_and_core_contracts(self) -> None:
        assert _module_imports() == {
            "__future__",
            "dataclasses",
            "collections.abc",
            "types",
            "typing",
            "agentx.core.errors",
            "agentx.core.result",
            "agentx.core.tasks",
        }

    def test_module_does_not_reach_outward_from_core(self) -> None:
        for forbidden in (
            "agentx.kernel",
            "agentx.infrastructure",
            "agentx.capabilities",
            "agentx.hive",
            "agentx.procedures",
            "agentx.cognition",
            "agentx.learning",
        ):
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in _module_imports()
            )

    def test_no_eventbus_persistence_or_execution_coupling(self) -> None:
        code = _code_without_docstrings()
        for forbidden in (
            "event_bus",
            "eventbus",
            "eventjournal",
            "publish",
            "subscribe",
            "sqlite",
            "persistence",
            "subprocess",
            "threading",
            "asyncio",
            "exec(",
            "eval(",
            "schedul",
            "kernel",
        ):
            assert forbidden not in code

    def test_no_forbidden_runtime_types_are_defined(self) -> None:
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        defined = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef | ast.FunctionDef)
        }
        forbidden = {
            "TaskManager",
            "TaskStore",
            "EventBus",
            "EventJournal",
            "Executor",
            "Planner",
            "Scheduler",
            "RetryEngine",
            "CancellationToken",
            "PermissionEngine",
            "ActionGate",
            "TaskStateMachine",
        }
        assert forbidden.isdisjoint(defined)

    def test_module_defines_no_state_machine_object(self) -> None:
        """The contract is pure functions; there is nothing stateful to hold."""
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        assert classes == {"InvalidTaskTransitionError"}

    def test_public_api_is_exactly_the_transition_contract(self) -> None:
        module = importlib.import_module("agentx.core.task_state")

        assert set(module.__all__) == {
            "LEGAL_TASK_TRANSITIONS",
            "TASK_TRANSITION_ERROR_CODE",
            "TERMINAL_TASK_STATUSES",
            "InvalidTaskTransitionError",
            "can_transition",
            "is_terminal",
            "legal_transitions",
            "transition_error",
            "transition_task",
            "try_transition_task",
            "validate_transition",
        }

    def test_task_schema_is_not_duplicated_or_extended(self) -> None:
        """A1.05 still owns Task and TaskStatus; A1.06 adds no vocabulary."""
        assert TaskStatus.__module__ == "agentx.core.tasks"
        assert Task.__module__ == "agentx.core.tasks"
        assert not hasattr(Task, "transition")
        assert not hasattr(Task, "can_transition")
        assert [member.value for member in TaskStatus] == [
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        ]
