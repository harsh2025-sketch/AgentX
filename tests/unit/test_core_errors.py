"""Tests for the structured AgentX error model (``agentx.core.errors``).

Covers:
    - error construction and validation
    - error taxonomy (categories)
    - retryability
    - safe serialization (no tracebacks/secrets in to_dict)
    - immutability
    - exception wrapper
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from agentx.core.errors import (
    AgentXError,
    AgentXException,
    ErrorCategory,
    Retryability,
)

# ---------------------------------------------------------------------------
# Error construction and validation
# ---------------------------------------------------------------------------


class TestErrorConstruction:
    def test_minimal_construction(self) -> None:
        err = AgentXError(
            code="task.not_found",
            message="Task 42 does not exist.",
            category=ErrorCategory.NOT_FOUND,
        )
        assert err.code == "task.not_found"
        assert err.message == "Task 42 does not exist."
        assert err.category == ErrorCategory.NOT_FOUND
        assert err.retryability == Retryability.UNKNOWN
        assert err.details == {}
        assert err.cause is None

    def test_full_construction(self) -> None:
        cause = RuntimeError("underlying failure")
        err = AgentXError(
            code="task.execution_failed",
            message="Task crashed.",
            category=ErrorCategory.EXECUTION,
            retryability=Retryability.RETRYABLE,
            details={"task_id": "abc-123", "attempt": 3},
            cause=cause,
        )
        assert err.code == "task.execution_failed"
        assert err.message == "Task crashed."
        assert err.category == ErrorCategory.EXECUTION
        assert err.retryability == Retryability.RETRYABLE
        assert err.details == {"task_id": "abc-123", "attempt": 3}
        assert err.cause is cause

    def test_rejects_empty_code(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            AgentXError(code="", message="msg", category=ErrorCategory.INTERNAL)

    def test_rejects_whitespace_code(self) -> None:
        with pytest.raises(ValueError, match="trimmed"):
            AgentXError(code=" task.x ", message="msg", category=ErrorCategory.INTERNAL)

    def test_rejects_code_with_control_chars(self) -> None:
        with pytest.raises(ValueError, match="control characters"):
            AgentXError(code="task\n.x", message="msg", category=ErrorCategory.INTERNAL)

    def test_rejects_non_string_code(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            AgentXError(code=123, message="msg", category=ErrorCategory.INTERNAL)  # type: ignore[arg-type]

    def test_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            AgentXError(code="x", message="", category=ErrorCategory.INTERNAL)

    def test_rejects_non_string_message(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            AgentXError(code="x", message=123, category=ErrorCategory.INTERNAL)  # type: ignore[arg-type]

    def test_rejects_invalid_category(self) -> None:
        with pytest.raises(ValueError, match="must be an ErrorCategory"):
            AgentXError(code="x", message="msg", category="not_found")  # type: ignore[arg-type]

    def test_rejects_invalid_retryability(self) -> None:
        with pytest.raises(ValueError, match="must be a Retryability"):
            AgentXError(
                code="x",
                message="msg",
                category=ErrorCategory.INTERNAL,
                retryability="retryable",  # type: ignore[arg-type]
            )

    def test_rejects_non_dict_details(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            AgentXError(
                code="x",
                message="msg",
                category=ErrorCategory.INTERNAL,
                details="not-a-dict",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    def test_all_expected_categories_exist(self) -> None:
        expected = {
            "validation",
            "not_found",
            "conflict",
            "precondition",
            "permission",
            "resource",
            "timeout",
            "cancelled",
            "dependency",
            "execution",
            "verification",
            "internal",
        }
        actual = {cat.value for cat in ErrorCategory}
        assert actual == expected

    def test_retryability_values(self) -> None:
        assert Retryability.RETRYABLE.value == "retryable"
        assert Retryability.NON_RETRYABLE.value == "non_retryable"
        assert Retryability.UNKNOWN.value == "unknown"

    def test_category_is_string_enum(self) -> None:
        assert isinstance(ErrorCategory.NOT_FOUND, str)
        assert ErrorCategory.NOT_FOUND.value == "not_found"


# ---------------------------------------------------------------------------
# Immutability of details
# ---------------------------------------------------------------------------


class TestDetailsImmutability:
    def test_details_returns_mapping_proxy(self) -> None:
        err = AgentXError(
            code="x",
            message="msg",
            category=ErrorCategory.INTERNAL,
            details={"key": "value"},
        )
        assert isinstance(err.details, MappingProxyType)

    def test_details_mutation_does_not_affect_error(self) -> None:
        original = {"key": "value"}
        err = AgentXError(
            code="x",
            message="msg",
            category=ErrorCategory.INTERNAL,
            details=original,
        )
        # Mutating the original dict after construction
        original["key"] = "mutated"
        # The error's details are a frozen copy
        assert err.details["key"] == "value"

    def test_empty_details_when_none(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        assert err.details == {}


# ---------------------------------------------------------------------------
# Safe serialization
# ---------------------------------------------------------------------------


class TestErrorSerialization:
    def test_to_dict_includes_core_fields(self) -> None:
        err = AgentXError(
            code="task.timeout",
            message="Task took too long.",
            category=ErrorCategory.TIMEOUT,
            retryability=Retryability.RETRYABLE,
        )
        d = err.to_dict()
        assert d["code"] == "task.timeout"
        assert d["message"] == "Task took too long."
        assert d["category"] == "timeout"
        assert d["retryability"] == "retryable"

    def test_to_dict_omits_details_when_empty(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        d = err.to_dict()
        assert "details" not in d

    def test_to_dict_includes_details_when_present(self) -> None:
        err = AgentXError(
            code="x",
            message="msg",
            category=ErrorCategory.INTERNAL,
            details={"key": "value"},
        )
        d = err.to_dict()
        assert d["details"] == {"key": "value"}

    def test_to_dict_excludes_cause(self) -> None:
        """Cause exceptions must never appear in serialized output."""
        cause = RuntimeError("secret internal detail")
        err = AgentXError(
            code="x",
            message="msg",
            category=ErrorCategory.INTERNAL,
            cause=cause,
        )
        d = err.to_dict()
        assert "cause" not in d
        # Even stringifying the dict must not leak the cause
        import json

        serialized = json.dumps(d)
        assert "secret internal detail" not in serialized

    def test_to_dict_can_strip_details(self) -> None:
        err = AgentXError(
            code="x",
            message="msg",
            category=ErrorCategory.INTERNAL,
            details={"secret": "value"},
        )
        d = err.to_dict(include_details=False)
        assert "details" not in d

    def test_to_dict_output_is_json_serializable(self) -> None:
        import json

        err = AgentXError(
            code="task.not_found",
            message="Task not found.",
            category=ErrorCategory.NOT_FOUND,
            retryability=Retryability.NON_RETRYABLE,
            details={"task_id": "abc"},
        )
        # Must not raise
        serialized = json.dumps(err.to_dict())
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# Equality and hashing
# ---------------------------------------------------------------------------


class TestErrorEquality:
    def test_equal_errors(self) -> None:
        a = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        b = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        assert a == b

    def test_unequal_code(self) -> None:
        a = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        b = AgentXError(code="y", message="msg", category=ErrorCategory.INTERNAL)
        assert a != b

    def test_unequal_category(self) -> None:
        a = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        b = AgentXError(code="x", message="msg", category=ErrorCategory.NOT_FOUND)
        assert a != b

    def test_hash_consistent_with_equality(self) -> None:
        a = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        b = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        assert hash(a) == hash(b)

    def test_cause_does_not_affect_equality(self) -> None:
        """Two errors with identical fields but different causes are still equal."""
        cause_a = RuntimeError("a")
        cause_b = RuntimeError("b")
        a = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL, cause=cause_a)
        b = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL, cause=cause_b)
        assert a == b

    def test_not_equal_to_non_error(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        assert err.__eq__("string") is NotImplemented


# ---------------------------------------------------------------------------
# Repr and str
# ---------------------------------------------------------------------------


class TestErrorRepr:
    def test_repr(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        r = repr(err)
        assert "AgentXError" in r
        assert "'x'" in r
        assert "'msg'" in r

    def test_str(self) -> None:
        err = AgentXError(
            code="task.not_found",
            message="No such task.",
            category=ErrorCategory.NOT_FOUND,
        )
        assert str(err) == "[task.not_found] No such task."


# ---------------------------------------------------------------------------
# Exception wrapper
# ---------------------------------------------------------------------------


class TestAgentXException:
    def test_wraps_error(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        exc = AgentXException(err)
        assert exc.error is err
        assert str(exc) == str(err)

    def test_is_exception(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        exc = AgentXException(err)
        assert isinstance(exc, Exception)

    def test_rejects_non_error(self) -> None:
        with pytest.raises(TypeError, match="requires an AgentXError"):
            AgentXException("not an error")  # type: ignore[arg-type]

    def test_can_be_raised_and_caught(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        with pytest.raises(AgentXException) as exc_info:
            raise AgentXException(err)
        assert exc_info.value.error is err

    def test_repr(self) -> None:
        err = AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)
        exc = AgentXException(err)
        assert "AgentXException" in repr(exc)
