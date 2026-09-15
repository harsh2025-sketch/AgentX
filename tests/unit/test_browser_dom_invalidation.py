"""AX-408 navigation invalidation coverage for canonical DOM snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentx.capabilities.browser_connection import (
    BrowserConnectionRef,
    BrowserConnectionState,
    BrowserSessionId,
    BrowserTargetId,
    BrowserTargetKind,
    BrowserTargetRef,
    BrowserTargetState,
)
from agentx.capabilities.browser_dom import (
    BrowserDomObservation,
    BrowserDomObservationState,
)
from agentx.capabilities.browser_dom_invalidation import (
    BrowserDomInvalidationReason,
    invalidate_dom_after_navigation,
)
from agentx.capabilities.browser_provider import BrowserProviderId

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _target(
    *,
    url: str | None = "https://example.invalid/a",
    state: BrowserTargetState = BrowserTargetState.AVAILABLE,
) -> BrowserTargetRef:
    provider = BrowserProviderId("browser.chromium")
    session = BrowserSessionId(provider_id=provider, value="session-1")
    connection = BrowserConnectionRef(
        session_id=session,
        state=BrowserConnectionState.CONNECTED,
        detail="test",
    )
    return BrowserTargetRef(
        connection=connection,
        target_id=BrowserTargetId(session_id=session, value="target-1"),
        kind=BrowserTargetKind.PAGE,
        state=state,
        title="Example",
        url=url,
    )


def _snapshot(target: BrowserTargetRef) -> BrowserDomObservation:
    return BrowserDomObservation(
        target=target,
        state=BrowserDomObservationState.OBSERVED,
        observed_at=_T0,
        document_version="document-1",
    )


def test_url_navigation_marks_existing_snapshot_stale_with_evidence() -> None:
    snapshot = _snapshot(_target())
    result = invalidate_dom_after_navigation(
        snapshot,
        current_target=_target(url="https://example.invalid/b"),
        observed_at=_T0 + timedelta(seconds=1),
        current_document_version="document-2",
    )

    assert result.invalidated
    assert result.observation.state is BrowserDomObservationState.STALE
    assert result.evidence is not None
    assert set(result.evidence.reasons) == {
        BrowserDomInvalidationReason.URL_CHANGED,
        BrowserDomInvalidationReason.DOCUMENT_VERSION_CHANGED,
    }
    assert snapshot.state is BrowserDomObservationState.OBSERVED


def test_same_known_navigation_identity_does_not_claim_verified_freshness() -> None:
    target = _target()
    snapshot = _snapshot(target)
    result = invalidate_dom_after_navigation(
        snapshot,
        current_target=target,
        observed_at=_T0 + timedelta(seconds=1),
        current_document_version="document-1",
    )

    assert not result.invalidated
    assert result.evidence is None
    assert result.observation is snapshot


def test_missing_navigation_metadata_is_not_invented_as_change() -> None:
    snapshot = _snapshot(_target(url=None))
    result = invalidate_dom_after_navigation(
        snapshot,
        current_target=_target(url=None),
        observed_at=_T0 + timedelta(seconds=1),
        current_document_version=None,
    )

    assert result.evidence is None
    assert result.observation.state is BrowserDomObservationState.OBSERVED


def test_unavailable_target_invalidates_even_without_url_change() -> None:
    snapshot = _snapshot(_target())
    unavailable = _target(state=BrowserTargetState.UNAVAILABLE)
    result = invalidate_dom_after_navigation(
        snapshot,
        current_target=unavailable,
        observed_at=_T0 + timedelta(seconds=1),
    )

    assert result.evidence is not None
    assert BrowserDomInvalidationReason.TARGET_UNAVAILABLE in result.evidence.reasons
    assert result.observation.state is BrowserDomObservationState.STALE
