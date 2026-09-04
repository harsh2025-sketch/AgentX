"""Typed Result abstraction for expected operational outcomes.

A ``Result[T, E]`` is a discriminated union representing either:
    - ``Success(value: T)`` — the operation produced a value.
    - ``Failure(error: E)`` — the operation produced a structured error.

It is impossible to construct a ``Result`` that is simultaneously success and
failure. Type narrowing is supported via ``is_success`` / ``is_failure``
properties combined with ``unwrap()`` / ``unwrap_error()``.

Policy — exceptions vs. results:

    Exceptions are for programming errors and violated contracts. Results are
    for expected operational outcomes that cross subsystem boundaries. When a
    function's failure modes are part of its contract (not found, timeout,
    precondition violation), it should return ``Result`` rather than raising.

    ``unwrap()`` and ``unwrap_error()`` are convenience methods for cases
    where the caller is certain of the outcome variant — they raise
    ``InvalidResultError`` (a programming error) if used on the wrong variant.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar, final

__all__ = [
    "Failure",
    "InvalidResultError",
    "Result",
    "Success",
]

T = TypeVar("T")
E = TypeVar("E")

# Sentinel for distinguishing "no value set" from "value is None"
_UNSET: Any = object()


class InvalidResultError(Exception):
    """Raised when a Result method is called on the wrong variant.

    This is a programming error — the caller assumed a success when the result
    was a failure, or vice versa.
    """


class Result(Generic[T, E]):  # noqa: UP046
    """Discriminated union of Success[T] or Failure[E].

    Construct via the class methods ``Result.success(value)`` or
    ``Result.failure(error)``. Do not instantiate directly.
    """

    __slots__ = ("_error", "_is_success", "_value")

    _is_success: bool
    _value: Any  # T or _UNSET sentinel
    _error: Any  # E or _UNSET sentinel

    def __init__(self, _is_success: bool, value: Any, error: Any) -> None:
        # Private constructor — use the class methods instead.
        object.__setattr__(self, "_is_success", _is_success)
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_error", error)

    @classmethod
    def success(cls, value: T) -> Success[T, E]:
        """Create a successful result containing *value*."""
        return Success[T, E](value)

    @classmethod
    def failure(cls, error: E) -> Failure[T, E]:
        """Create a failed result containing *error*."""
        return Failure[T, E](error)

    @property
    def is_success(self) -> bool:
        """True if this result is a Success."""
        return self._is_success

    @property
    def is_failure(self) -> bool:
        """True if this result is a Failure."""
        return not self._is_success

    def unwrap(self) -> T:
        """Return the success value.

        Raises ``InvalidResultError`` if this is a Failure — that is a
        programming error.
        """
        if not self._is_success:
            raise InvalidResultError(f"Called unwrap() on a Failure: {self._error!r}")
        return self._value  # type: ignore[no-any-return]

    def unwrap_error(self) -> E:
        """Return the failure error.

        Raises ``InvalidResultError`` if this is a Success — that is a
        programming error.
        """
        if self._is_success:
            raise InvalidResultError("Called unwrap_error() on a Success")
        return self._error  # type: ignore[no-any-return]

    def unwrap_or(self, default: T) -> T:
        """Return the success value, or *default* if this is a Failure."""
        if self._is_success:
            return self._value  # type: ignore[no-any-return]
        return default

    # No __bool__ — Result must not have implicit truthiness that could
    # hide a failure in a boolean context.

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Result):
            return NotImplemented
        if self._is_success != other._is_success:
            return False
        if self._is_success:
            return bool(self._value == other._value)
        return bool(self._error == other._error)

    def __hash__(self) -> int:
        if self._is_success:
            return hash(("Success", self._value))
        return hash(("Failure", self._error))

    def __repr__(self) -> str:
        if self._is_success:
            return f"Success({self._value!r})"
        return f"Failure({self._error!r})"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Result instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Result instances are immutable")


@final
class Success(Result[T, E]):
    """A successful result containing a value.

    Prefer ``Result.success(value)`` — this class is exported for isinstance
    checks and type narrowing.
    """

    def __init__(self, value: T) -> None:
        super().__init__(_is_success=True, value=value, error=_UNSET)


@final
class Failure(Result[T, E]):
    """A failed result containing an error.

    Prefer ``Result.failure(error)`` — this class is exported for isinstance
    checks and type narrowing.
    """

    def __init__(self, error: E) -> None:
        if error is None:
            raise ValueError("Failure error must not be None")
        super().__init__(_is_success=False, value=_UNSET, error=error)
