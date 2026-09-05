"""Test support: a ``sys.modules`` stand-in that records and rejects touches.

Used by A3.03 node-family contract tests to prove that constructing,
validating, serializing, and deserializing a contract never reaches an
authority or runtime subsystem: any attribute access on a proxied module
records the touch and raises, failing the test.
"""

from __future__ import annotations


class ForbiddenAuthorityProxy:
    """A stand-in module object that fails the test on any attribute access."""

    def __init__(self, name: str, touched: list[tuple[str, str]]) -> None:
        self._name = name
        self._touched = touched

    def _record(self, attr: str) -> object:
        self._touched.append((self._name, attr))
        raise AssertionError(
            f"authority module {self._name!r} must not be touched by the node-family "
            f"contract (accessed {attr!r})"
        )

    def __getattr__(self, name: str) -> object:
        return self._record(name)

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        return self._record("__call__")

    def __bool__(self) -> bool:
        return False
