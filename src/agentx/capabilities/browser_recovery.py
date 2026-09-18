"""Fail-closed browser target recovery after a DOM change.

Recovery is read-only and deterministic. It performs one fresh canonical DOM
observation and reapplies one existing deterministic BrowserDomSelector.
Only a UNIQUE fresh match is returned. No fuzzy matching, model call,
execution, or authority change occurs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentx.capabilities.browser_dom import (
    BrowserDomNodeSnapshot,
    BrowserDomObservation,
    BrowserDomReadRequest,
)
from agentx.capabilities.browser_selection import (
    BrowserDomSelectionStatus,
    BrowserDomSelector,
    select_dom_nodes,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = ["BrowserSelectionRecovery", "BrowserSelectionRecoveryReader"]


class BrowserSelectionRecoveryReader(Protocol):
    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]: ...


@dataclass(frozen=True, slots=True)
class BrowserSelectionRecovery:
    reader: BrowserSelectionRecoveryReader

    def recover(
        self,
        request: BrowserDomReadRequest,
        selector: BrowserDomSelector,
    ) -> Result[BrowserDomNodeSnapshot, AgentXError]:
        observed = self.reader.observe_dom(request)
        if observed.is_failure:
            return Result.failure(observed.unwrap_error())
        selection = select_dom_nodes(observed.unwrap(), selector)
        if selection.status is not BrowserDomSelectionStatus.UNIQUE:
            return Result.failure(
                AgentXError(
                    code="browser.recovery.target_not_unique",
                    message="fresh DOM recovery did not resolve exactly one target",
                    category=ErrorCategory.PRECONDITION,
                    retryability=Retryability.RETRYABLE,
                    details={"selection_status": selection.status.value},
                )
            )
        match = selection.unique_match
        assert match is not None
        return Result.success(match)
