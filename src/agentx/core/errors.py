"""Structured error model for AgentX domain failures.

This module defines:

- ``ErrorCategory`` — a controlled taxonomy of failure categories.
- ``Retryability`` — whether an error is safe to retry.
- ``AgentXError`` — an immutable, structured domain error value that can be
  carried across subsystem boundaries inside a ``Result`` or raised as an
  exception when immediate propagation is needed.

Policy — exceptions vs. results:

    - **Exceptions** (``raise``) are for programming errors: violated contracts,
      malformed arguments, unexpected internal state. They should not cross
      subsystem boundaries as part of normal control flow.

    - **Results** (``Result.success(value)`` / ``Result.failure(error)``) are
      for expected operational outcomes that cross subsystem boundaries: a task
      failing, a capability not found, a timeout, a precondition violation.
      Returning a ``Result`` makes failure explicit and forces the caller to
      handle it.

``AgentXError`` is an immutable *value* — it can be embedded in a ``Failure``
result or raised as an ``AgentXException`` when the calling code prefers
exceptional control flow. Serialization via ``to_dict()`` strips unsafe
metadata (tracebacks, secrets) by default.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Any

__all__ = [
    "AgentXError",
    "AgentXException",
    "ErrorCategory",
    "Retryability",
]


class ErrorCategory(StrEnum):
    """Controlled top-level taxonomy of AgentX failure categories.

    This taxonomy is intentionally small. New categories should be added here
    only when a real subsystem requires them — do not pre-create speculative
    categories for subsystems that don't exist yet.
    """

    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PRECONDITION = "precondition"
    PERMISSION = "permission"
    RESOURCE = "resource"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEPENDENCY = "dependency"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    INTERNAL = "internal"


class Retryability(StrEnum):
    """Whether an error is safe or meaningful to retry."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    UNKNOWN = "unknown"


def _validate_error_code(value: str) -> str:
    """Validate that an error code is a non-empty, trimmed, dot-separated string."""
    if not isinstance(value, str):
        raise ValueError(f"error code must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("error code must not be empty")
    if stripped != value:
        raise ValueError(f"error code must be trimmed: {value!r}")
    if any(c in stripped for c in ("\x00", "\n", "\r", "\t")):
        raise ValueError(f"error code must not contain control characters: {value!r}")
    return stripped


def _validate_message(value: str) -> str:
    """Validate that a message is a non-empty, trimmed string."""
    if not isinstance(value, str):
        raise ValueError(f"message must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("message must not be empty")
    return stripped


def _freeze_details(details: dict[str, Any] | None) -> MappingProxyType[str, Any]:
    """Create an immutable, shallow-frozen copy of the details mapping."""
    if details is None:
        return MappingProxyType({})
    if not isinstance(details, dict):
        raise ValueError(f"details must be a dict or None, got {type(details).__name__}")
    return MappingProxyType(dict(details))


class AgentXError:
    """Immutable structured domain error value.

    This is a *value type* — it represents an operational failure that can be
    carried in a ``Result.failure(error)`` or raised as ``AgentXException``.

    Attributes:
        code: A dot-separated error code (e.g. "task.not_found").
        message: A human-readable description.
        category: The controlled failure category.
        retryability: Whether this error is safe to retry.
        details: Immutable mapping of additional context. Never contains
            tracebacks, secrets, or arbitrary internal state in serialization.
        cause: An optional wrapped exception for diagnostics. Excluded from
            serialization to prevent leaking internal state.
    """

    __slots__ = ("_category", "_cause", "_code", "_details", "_message", "_retryability")

    def __init__(
        self,
        *,
        code: str,
        message: str,
        category: ErrorCategory,
        retryability: Retryability = Retryability.UNKNOWN,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self._code = _validate_error_code(code)
        self._message = _validate_message(message)
        if not isinstance(category, ErrorCategory):
            raise ValueError(f"category must be an ErrorCategory, got {type(category).__name__}")
        self._category = category
        if not isinstance(retryability, Retryability):
            raise ValueError(
                f"retryability must be a Retryability, got {type(retryability).__name__}"
            )
        self._retryability = retryability
        self._details = _freeze_details(details)
        self._cause = cause

    @property
    def code(self) -> str:
        return self._code

    @property
    def message(self) -> str:
        return self._message

    @property
    def category(self) -> ErrorCategory:
        return self._category

    @property
    def retryability(self) -> Retryability:
        return self._retryability

    @property
    def details(self) -> MappingProxyType[str, Any]:
        return self._details

    @property
    def cause(self) -> BaseException | None:
        """The wrapped exception, if any. Excluded from serialization."""
        return self._cause

    def to_dict(self, *, include_details: bool = True) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        By default, details are included. Set ``include_details=False`` to
        strip all detail metadata (e.g. for external API responses). The
        ``cause`` exception is never serialized.
        """
        result: dict[str, Any] = {
            "code": self._code,
            "message": self._message,
            "category": self._category.value,
            "retryability": self._retryability.value,
        }
        if include_details and self._details:
            result["details"] = dict(self._details)
        return result

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AgentXError):
            return NotImplemented
        return (
            self._code == other._code
            and self._message == other._message
            and self._category == other._category
            and self._retryability == other._retryability
            and self._details == other._details
        )

    def __hash__(self) -> int:
        return hash((self._code, self._message, self._category, self._retryability))

    def __repr__(self) -> str:
        return (
            f"AgentXError(code={self._code!r}, message={self._message!r}, "
            f"category={self._category.value!r}, retryability={self._retryability.value!r})"
        )

    def __str__(self) -> str:
        return f"[{self._code}] {self._message}"


class AgentXException(Exception):  # noqa: N818
    """Exception wrapper for an ``AgentXError``.

    Use this when you need to raise a domain error as an exception rather than
    returning it in a ``Result``. The underlying ``AgentXError`` is preserved
    as the ``error`` attribute and as ``__cause__`` chain support.

    This is still an exception — it is for cases where raising is the
    appropriate control flow. For expected cross-boundary failures, prefer
    ``Result.failure(error)``.
    """

    def __init__(self, error: AgentXError) -> None:
        if not isinstance(error, AgentXError):
            raise TypeError(f"AgentXException requires an AgentXError, got {type(error).__name__}")
        super().__init__(str(error))
        self._error = error

    @property
    def error(self) -> AgentXError:
        return self._error

    def __repr__(self) -> str:
        return f"AgentXException({self._error!r})"
