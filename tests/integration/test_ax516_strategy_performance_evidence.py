"""AX-516 durable strategy-performance evidence acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from agentx.cognition.router import ExecutionLevel
from agentx.core.events import Event, EventType, ObservationPayload
from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.execution_metrics import ExecutionMetricsRecord
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.strategy_performance_evidence import (
    MAX_STRATEGY_EVIDENCE_READ,
    StrategyPerformanceEvidence,
    StrategyPerformanceEvidenceError,
    StrategyPerformanceLedger,
)

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _metrics(level: ExecutionLevel, *, task_id: TaskId | None = None) -> ExecutionMetricsRecord:
    return ExecutionMetricsRecord(
        task_id=task_id or TaskId.create(),
        correlation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        execution_level=level,
        model_calls=0,
        elapsed=timedelta(milliseconds=25),
        external_cost=Decimal("0"),
        cost_unit="USD",
        machine_actions=1,
        outcome=ExecutionEvidenceOutcome.UNVERIFIED,
    )


def _ledger(path: Path) -> StrategyPerformanceLedger:
    return StrategyPerformanceLedger(EventJournal(SQLiteDatabase(path.resolve())))


def test_strategy_evidence_survives_restart_and_filters_by_exact_level(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    first = _ledger(path)
    l1 = StrategyPerformanceEvidence.from_metrics(
        _metrics(ExecutionLevel.L1_DIRECT),
        observed_at=_T0,
        evidence_id=UUID("11111111-1111-4111-8111-111111111111"),
    )
    l3 = StrategyPerformanceEvidence.from_metrics(
        _metrics(ExecutionLevel.L3_GUIDED),
        observed_at=_T0,
        evidence_id=UUID("33333333-3333-4333-8333-333333333333"),
    )
    first.append(l1)
    first.append(l3)

    restarted = _ledger(path)
    assert tuple(entry.evidence for entry in restarted.read()) == (l1, l3)
    assert tuple(
        entry.evidence for entry in restarted.read(execution_level=ExecutionLevel.L3_GUIDED)
    ) == (l3,)


def test_unrelated_journal_events_remain_unrelated_and_order_is_durable(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    journal = EventJournal(SQLiteDatabase(path.resolve()))
    journal.append(
        Event.create(
            event_type=EventType.OBSERVATION_RECORDED,
            source="other.subsystem",
            payload=ObservationPayload(value={"permission": "ADMIN"}),
            timestamp=_T0,
        )
    )
    evidence = StrategyPerformanceEvidence.from_metrics(
        _metrics(ExecutionLevel.L2_COMPILED),
        observed_at=_T0,
    )
    StrategyPerformanceLedger(journal).append(evidence)

    entries = StrategyPerformanceLedger(EventJournal(SQLiteDatabase(path.resolve()))).read()
    assert tuple(entry.evidence for entry in entries) == (evidence,)
    assert entries[0].sequence == 2


def test_historical_evidence_is_inert_and_cannot_manufacture_verified_success(tmp_path: Path) -> None:
    metrics = _metrics(ExecutionLevel.L5_EXPLORATORY)
    evidence = StrategyPerformanceEvidence.from_metrics(metrics, observed_at=_T0)
    assert evidence.outcome is ExecutionEvidenceOutcome.UNVERIFIED
    assert evidence.verification_passed is None
    assert evidence.execution_level is ExecutionLevel.L5_EXPLORATORY

    ledger = _ledger(tmp_path / "agentx.sqlite3")
    ledger.append(evidence)
    restored = ledger.read()[0].evidence
    assert restored == evidence
    assert restored.outcome is ExecutionEvidenceOutcome.UNVERIFIED


def test_read_boundaries_fail_closed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "agentx.sqlite3")
    with pytest.raises(StrategyPerformanceEvidenceError, match="limit"):
        ledger.read(limit=0)
    with pytest.raises(StrategyPerformanceEvidenceError, match="limit"):
        ledger.read(limit=MAX_STRATEGY_EVIDENCE_READ + 1)
    with pytest.raises(StrategyPerformanceEvidenceError, match="max_scan"):
        ledger.read(max_scan=0)
