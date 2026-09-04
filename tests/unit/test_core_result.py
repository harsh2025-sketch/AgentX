"""Tests for the typed Result abstraction (``agentx.core.result``).

Covers:
    - success construction
    - failure construction
    - type/discriminator behavior
    - no ambiguous success+failure state
    - unwrap / unwrap_error / unwrap_or
    - forbidden truthiness
    - immutability
    - equality and hashing
    - isinstance narrowing
"""

from __future__ import annotations

import pytest

from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.result import Failure, InvalidResultError, Result, Success


def _make_err() -> AgentXError:
    return AgentXError(code="x", message="msg", category=ErrorCategory.INTERNAL)


# ---------------------------------------------------------------------------
# Success construction
# ---------------------------------------------------------------------------


class TestSuccessConstruction:
    def test_result_success_creates_success(self) -> None:
        r: Result[int, str] = Result.success(42)
        assert r.is_success is True
        assert r.is_failure is False

    def test_success_class_creates_success(self) -> None:
        r: Result[int, str] = Success(42)
        assert r.is_success is True
        assert r.is_failure is False

    def test_success_unwrap_returns_value(self) -> None:
        r: Result[str, str] = Result.success("hello")
        assert r.unwrap() == "hello"

    def test_success_with_none_value(self) -> None:
        r: Result[None, str] = Result.success(None)
        assert r.is_success is True
        assert r.unwrap() is None

    def test_success_with_complex_value(self) -> None:
        r: Result[dict[str, list[int]], str] = Result.success({"key": [1, 2, 3]})
        assert r.unwrap() == {"key": [1, 2, 3]}


# ---------------------------------------------------------------------------
# Failure construction
# ---------------------------------------------------------------------------


class TestFailureConstruction:
    def test_result_failure_creates_failure(self) -> None:
        r: Result[int, AgentXError] = Result.failure(_make_err())
        assert r.is_failure is True
        assert r.is_success is False

    def test_failure_class_creates_failure(self) -> None:
        r: Result[int, AgentXError] = Failure(_make_err())
        assert r.is_failure is True
        assert r.is_success is False

    def test_failure_unwrap_error_returns_error(self) -> None:
        err = _make_err()
        r: Result[int, AgentXError] = Result.failure(err)
        assert r.unwrap_error() is err

    def test_failure_rejects_none_error(self) -> None:
        with pytest.raises(ValueError, match="must not be None"):
            Result.failure(None)

    def test_failure_class_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="must not be None"):
            Failure(None)

    def test_failure_with_string_error(self) -> None:
        r: Result[int, str] = Result.failure("something went wrong")
        assert r.unwrap_error() == "something went wrong"


# ---------------------------------------------------------------------------
# Type/discriminator behavior — no ambiguous state
# ---------------------------------------------------------------------------


class TestDiscriminator:
    def test_success_is_not_failure(self) -> None:
        r: Result[int, str] = Result.success(42)
        assert r.is_success is True
        assert r.is_failure is False

    def test_failure_is_not_success(self) -> None:
        r: Result[int, str] = Result.failure("err")
        assert r.is_failure is True
        assert r.is_success is False

    def test_success_and_failure_are_mutually_exclusive(self) -> None:
        """It's impossible for a Result to be both success and failure."""
        s: Result[int, str] = Result.success(42)
        f: Result[int, str] = Result.failure("err")
        assert s.is_success != s.is_failure
        assert f.is_success != f.is_failure

    def test_isinstance_success(self) -> None:
        s: Result[int, str] = Result.success(42)
        assert isinstance(s, Result)
        assert isinstance(s, Success)
        # Success and Failure are final subclasses; verify discriminator flag
        assert s.is_success is True

    def test_isinstance_failure(self) -> None:
        f: Result[int, str] = Result.failure("err")
        assert isinstance(f, Result)
        assert isinstance(f, Failure)
        assert f.is_failure is True


# ---------------------------------------------------------------------------
# Unwrap
# ---------------------------------------------------------------------------


class TestUnwrap:
    def test_unwrap_on_failure_raises(self) -> None:
        r: Result[int, str] = Result.failure("err")
        with pytest.raises(InvalidResultError, match=r"unwrap\(\) on a Failure"):
            r.unwrap()

    def test_unwrap_error_on_success_raises(self) -> None:
        r: Result[int, str] = Result.success(42)
        with pytest.raises(InvalidResultError, match=r"unwrap_error\(\) on a Success"):
            r.unwrap_error()

    def test_unwrap_or_returns_value_on_success(self) -> None:
        r: Result[int, str] = Result.success(42)
        assert r.unwrap_or(0) == 42

    def test_unwrap_or_returns_default_on_failure(self) -> None:
        r: Result[int, str] = Result.failure("err")
        assert r.unwrap_or(0) == 0


# ---------------------------------------------------------------------------
# Forbidden truthiness
# ---------------------------------------------------------------------------


class TestNoTruthiness:
    def test_result_does_not_define_bool(self) -> None:
        """Result must not define __bool__ to avoid hiding failures."""
        assert "__bool__" not in Result.__dict__

    def test_success_with_falsy_value_is_still_truthy(self) -> None:
        """A Success containing a falsy value must not be falsy."""
        r: Result[int, str] = Result.success(0)
        assert bool(r) is True

    def test_failure_is_truthy(self) -> None:
        """A Failure must not be falsy (no implicit truthiness distinction)."""
        r: Result[int, str] = Result.failure("err")
        assert bool(r) is True


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_cannot_set_attribute_on_success(self) -> None:
        r: Result[int, str] = Result.success(42)
        with pytest.raises(AttributeError, match="immutable"):
            r._value = 99

    def test_cannot_set_attribute_on_failure(self) -> None:
        r: Result[int, str] = Result.failure("err")
        with pytest.raises(AttributeError, match="immutable"):
            r._error = "other"

    def test_cannot_add_attribute(self) -> None:
        r: Result[int, str] = Result.success(42)
        with pytest.raises(AttributeError, match="immutable"):
            r.new_attr = "nope"

    def test_cannot_delete_attribute(self) -> None:
        r: Result[int, str] = Result.success(42)
        with pytest.raises(AttributeError, match="immutable"):
            del r._value


# ---------------------------------------------------------------------------
# Equality and hashing
# ---------------------------------------------------------------------------


class TestEquality:
    def test_equal_successes(self) -> None:
        a: Result[int, str] = Result.success(42)
        b: Result[int, str] = Result.success(42)
        assert a == b

    def test_unequal_successes(self) -> None:
        a: Result[int, str] = Result.success(42)
        b: Result[int, str] = Result.success(99)
        assert a != b

    def test_equal_failures(self) -> None:
        a: Result[int, str] = Result.failure("err")
        b: Result[int, str] = Result.failure("err")
        assert a == b

    def test_unequal_failures(self) -> None:
        a: Result[int, str] = Result.failure("err1")
        b: Result[int, str] = Result.failure("err2")
        assert a != b

    def test_success_not_equal_to_failure(self) -> None:
        s: Result[str, str] = Result.success("value")
        f: Result[str, str] = Result.failure("err")
        assert s != f

    def test_hash_equal_for_equal_results(self) -> None:
        a: Result[int, str] = Result.success(42)
        b: Result[int, str] = Result.success(42)
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self) -> None:
        results: set[Result[int, str]] = {
            Result.success(1),
            Result.success(2),
            Result.failure("a"),
        }
        assert len(results) == 3

    def test_not_equal_to_non_result(self) -> None:
        r: Result[int, str] = Result.success(42)
        assert r.__eq__(42) is NotImplemented


# ---------------------------------------------------------------------------
# Repr
# ---------------------------------------------------------------------------


class TestRepr:
    def test_success_repr(self) -> None:
        r: Result[int, str] = Result.success(42)
        assert repr(r) == "Success(42)"

    def test_failure_repr(self) -> None:
        r: Result[int, str] = Result.failure("err")
        assert repr(r) == "Failure('err')"


# ---------------------------------------------------------------------------
# InvalidResultError
# ---------------------------------------------------------------------------


class TestInvalidResultError:
    def test_is_exception(self) -> None:
        assert issubclass(InvalidResultError, Exception)

    def test_message(self) -> None:
        with pytest.raises(InvalidResultError, match="test message"):
            raise InvalidResultError("test message")
