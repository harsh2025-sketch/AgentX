"""Deliberate architecture violation used by the dependency-rule check test.

This module exists so ``tests/architecture/test_module_boundaries.py`` has a
real synthetic case demonstrating that the guardrail detects a forbidden
top-level import. It is intentionally placed under ``tests`` and must never be
imported by runtime code.

It violates the rule: ``agentx.learning`` must not import
``agentx.capabilities`` implementation internals (or, at top level, the
capabilities subsystem at all).
"""

from __future__ import annotations

import agentx.capabilities as _capabilities

# The import above is a check-provoked violation. Keep the reference so the
# synthetic import is intentionally present in the module namespace.
DELIBERATE_FORBIDDEN_IMPORT = _capabilities
